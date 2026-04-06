"""Generate security group JSON files from the local Wolfram kernel.

Run at install time via:
    mma-mcp setup

This queries the local kernel for all System` symbols and categorises them
into safe / dangerous groups.  Results are written to the security/groups/
directory inside the package, replacing the bundled defaults.

The dangerous-group strategy has two layers:
  1. Pattern-based discovery via Names["System`<pattern>"] + explicit seed lists.
     This always runs and does not require internet access.
  2. WolframLanguageData enrichment — queries Wolfram's official FunctionalityAreas
     classification for every System` symbol and merges the results.
     This layer is optional: if the network is unavailable it is skipped silently.

Layer 2 maps FunctionalityAreas to danger groups as follows:
  FileSystemSymbols       → file_read (read-flavoured names) or file_write (write-flavoured)
  ExternalProcessSymbols  → system_exec
  LibraryLinkSymbols      → system_exec
  URLSymbols              → networking
  SocketSymbols           → networking
  CloudSymbols            → external_services
  ServiceSymbols          → external_services
  StreamSymbols           → file_read
  PackageSymbols          → file_read
  CodeEvaluationSymbols   → dynamic_eval
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

GROUPS_DIR = Path(__file__).parent / "security" / "groups"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _short(name: str) -> str:
    return name.rsplit("`", 1)[-1]


def _write_group(name: str, symbols: set[str]) -> None:
    path = GROUPS_DIR / f"{name}.json"
    lst = sorted(symbols)
    path.write_text(json.dumps(lst, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {name}.json  ({len(lst)} symbols)")


# ---------------------------------------------------------------------------
# WolframLanguageData enrichment (optional, requires network)
# ---------------------------------------------------------------------------

# Maps FunctionalityArea → which danger group(s) to add to.
# FileSystemSymbols needs further splitting by name; handled in code.
_AREA_TO_GROUP: dict[str, list[str]] = {
    "ExternalProcessSymbols": ["system_exec"],
    "LibraryLinkSymbols":     ["system_exec"],
    "URLSymbols":             ["networking"],
    "SocketSymbols":          ["networking"],
    "CloudSymbols":           ["external_services"],
    "ServiceSymbols":         ["external_services"],
    "StreamSymbols":          ["file_read"],
    "PackageSymbols":         ["file_read"],
    "CodeEvaluationSymbols":  ["dynamic_eval"],
}

# Keywords that mark a FileSystemSymbols member as write-flavoured.
_WRITE_KEYWORDS = ("Write", "Export", "Put", "Save", "Copy", "Rename",
                   "Delete", "Create", "Set", "Move")


def _query_functionality_areas(
    session,
    all_sys: list[str],
    batch_size: int = 100,
) -> dict[str, set[str]]:
    """Query WolframLanguageData for all symbols and return {group: {symbols}}.

    Returns an empty dict if the network is unavailable or the query fails.
    """
    import time
    from wolframclient.language import wlexpr

    group_syms: dict[str, set[str]] = {g: set() for g in
        ["system_exec", "file_read", "file_write", "networking",
         "external_services", "dynamic_eval"]}

    try:
        total = len(all_sys)
        found = 0
        n_batches = (total + batch_size - 1) // batch_size
        t_start = time.time()
        print(f"  Querying WolframLanguageData for {total} symbols "
              f"({n_batches} batches of {batch_size})…", flush=True)

        failed_batches = 0
        for i in range(0, total, batch_size):
            batch = all_sys[i:i + batch_size]
            batch_num = i // batch_size + 1
            wl_list = "{" + ",".join(f'"{x}"' for x in batch) + "}"
            try:
                t0 = time.time()
                areas_list = session.evaluate(wlexpr(
                    f'WolframLanguageData[{wl_list}, "FunctionalityAreas"]'
                ))
                elapsed = time.time() - t0
            except Exception as exc:
                failed_batches += 1
                print(f"    batch {batch_num}/{n_batches} FAILED: {exc}", flush=True)
                continue
            batch_found = 0

            if hasattr(areas_list, '__iter__'):
                for sym, areas in zip(batch, areas_list):
                    if not (hasattr(areas, '__iter__') and not isinstance(areas, str)):
                        continue
                    for area in areas:
                        area_str = str(area).strip("'\"() ")
                        if "Missing" in area_str or not area_str:
                            continue
                        batch_found += 1
                        if area_str == "FileSystemSymbols":
                            if any(sym.startswith(kw) or kw in sym
                                   for kw in _WRITE_KEYWORDS):
                                group_syms["file_write"].add(sym)
                            else:
                                group_syms["file_read"].add(sym)
                        elif area_str in _AREA_TO_GROUP:
                            for grp in _AREA_TO_GROUP[area_str]:
                                group_syms[grp].add(sym)

            found += batch_found
            pct = batch_num * 100 // n_batches
            print(f"    batch {batch_num}/{n_batches} ({pct}%) "
                  f"+{batch_found} areas ({elapsed:.1f}s)", flush=True)

        total_time = time.time() - t_start
        total_classified = sum(len(s) for s in group_syms.values())
        if failed_batches:
            print(f"  Done (partial): {found} area assignments → {total_classified} symbols classified "
                  f"({failed_batches} batches failed, {total_time:.0f}s total)\n", flush=True)
        else:
            print(f"  Done: {found} area assignments → {total_classified} symbols classified "
                  f"({total_time:.0f}s total)\n", flush=True)

    except Exception as exc:
        print(f"\n  WolframLanguageData init failed ({exc}); skipping enrichment.\n",
              flush=True)
        return {}

    return group_syms


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_setup(kernel_path: str | None = None) -> None:
    """Generate all group JSON files from the local Wolfram kernel."""
    from wolframclient.evaluation import WolframLanguageSession
    from wolframclient.language import wlexpr

    from mma_mcp.kernel import find_kernel

    resolved = kernel_path or find_kernel()
    if resolved:
        print(f"Using kernel: {resolved}")
    else:
        print("Warning: could not auto-detect kernel, letting wolframclient try…")

    print("Starting Wolfram kernel…")
    with WolframLanguageSession(kernel=resolved) as session:
        print("Kernel ready.\n")

        # --- All System` short names (ground truth for this kernel version) ---
        all_sys: set[str] = {
            _short(s)
            for s in session.evaluate(wlexpr('Names["System`*"]'))
        }
        print(f"Total System` symbols: {len(all_sys)}\n")

        def by_pattern(*patterns: str) -> set[str]:
            """Return short names matching one or more wildcard patterns."""
            result: set[str] = set()
            for pat in patterns:
                found = session.evaluate(wlexpr(f'Names["System`{pat}"]'))
                result |= {_short(s) for s in found}
            return result & all_sys

        def from_seeds(*seed_lists: list[str]) -> set[str]:
            """For safe groups: only include symbols that exist in this kernel."""
            result: set[str] = set()
            for seeds in seed_lists:
                result |= {s for s in seeds if s in all_sys}
            return result

        def danger_seeds(*seed_lists: list[str]) -> set[str]:
            """For dangerous groups: include ALL named symbols regardless of
            whether they exist in the current kernel version.  A symbol that
            doesn't exist today may be added in a future upgrade, or may live
            in a non-System` context we don't enumerate."""
            result: set[str] = set()
            for seeds in seed_lists:
                result |= set(seeds)
            return result

        # ----------------------------------------------------------------
        # Safe groups
        # ----------------------------------------------------------------
        print("Generating safe groups…")

        # arithmetic: NumericFunction or Listable attributes
        # Run entirely inside WL to avoid wolframclient hanging on the
        # Select over 7000+ symbols.  Return only short names.
        raw_arith = session.evaluate(wlexpr(
            'Module[{syms = Names["System`*"], result},'
            '  result = Select[syms,'
            '    Quiet@Check['
            '      MemberQ[Attributes[Evaluate@ToExpression[#]],'
            '        NumericFunction|Listable], False]&];'
            '  Map[Last@StringSplit[#, "`"]&, result]'
            ']'
        ))
        print(f"    (kernel found {len(raw_arith) if hasattr(raw_arith, '__len__') else 0}"
              f" NumericFunction|Listable symbols)")
        arithmetic = set(raw_arith) if hasattr(raw_arith, '__iter__') else set()
        arithmetic = {str(s) for s in arithmetic} | from_seeds([
            "Pi", "E", "I", "Infinity", "DirectedInfinity", "Degree",
            "Sin", "Cos", "Tan", "Cot", "Sec", "Csc",
            "ArcSin", "ArcCos", "ArcTan", "ArcCot", "ArcSec", "ArcCsc",
            "Sinh", "Cosh", "Tanh", "ArcSinh", "ArcCosh", "ArcTanh",
            "Log", "Log2", "Log10", "Exp", "Sqrt", "CubeRoot",
            "Abs", "Sign", "Floor", "Ceiling", "Round",
            "IntegerPart", "FractionalPart", "Mod", "Quotient",
            "GCD", "LCM", "Max", "Min", "Total",
            "Re", "Im", "Conjugate", "Arg",
        ])
        _write_group("arithmetic", arithmetic)

        # algebra
        algebra = from_seeds([
            "Solve", "NSolve", "Reduce", "FindRoot", "Factor", "Expand",
            "ExpandAll", "ExpandNumerator", "ExpandDenominator",
            "Collect", "Cancel", "Together", "Apart", "PolynomialGCD",
            "PolynomialLCM", "PolynomialQuotient", "PolynomialRemainder",
            "PolynomialReduce", "PolynomialMod", "Roots", "NRoots",
            "RootReduce", "ToRadicals", "Decompose", "Discriminant",
            "Resultant", "GroebnerBasis", "PowerExpand", "ComplexExpand",
            "TrigExpand", "TrigFactor", "TrigReduce", "TrigToExp",
            "ExpToTrig", "Simplify", "FullSimplify", "Refine",
            "FunctionExpand", "PowerExpand", "Numerator", "Denominator",
            "Coefficient", "CoefficientList", "CoefficientRules",
            "Exponent", "Variables", "MonomialList",
        ])
        _write_group("algebra", algebra)

        # calculus
        calculus = from_seeds([
            "D", "Dt", "Derivative", "Integrate", "NIntegrate",
            "DSolve", "NDSolve", "NDSolveValue", "ParametricNDSolve",
            "Limit", "Series", "SeriesCoefficient", "Normal",
            "Residue", "InverseLaplaceTransform", "LaplaceTransform",
            "FourierTransform", "InverseFourierTransform",
            "ZTransform", "InverseZTransform",
            "Sum", "NSum", "Product", "NProduct",
            "Asymptotic", "AsymptoticIntegrate", "AsymptoticDSolveValue",
        ])
        _write_group("calculus", calculus)

        # linear_algebra
        linear_algebra = from_seeds([
            "LinearSolve", "NullSpace", "RowReduce", "MatrixRank",
            "Det", "Inverse", "Transpose", "ConjugateTranspose",
            "Dot", "Cross", "Norm", "Normalize",
            "Eigenvalues", "Eigenvectors", "Eigensystem",
            "SingularValueDecomposition", "LUDecomposition",
            "CholeskyDecomposition", "QRDecomposition",
            "SchurDecomposition", "HessenbergDecomposition",
            "MatrixExp", "MatrixLog", "MatrixPower", "MatrixFunction",
            "KroneckerProduct", "TensorProduct", "Tr",
            "DiagonalMatrix", "IdentityMatrix", "HilbertMatrix",
            "VandermondeMatrix", "ToeplitzMatrix", "HadamardMatrix",
        ])
        _write_group("linear_algebra", linear_algebra)

        # statistics
        statistics = from_seeds([
            "Mean", "Median", "Variance", "StandardDeviation",
            "Skewness", "Kurtosis", "Quantile", "Quartiles",
            "InterquartileRange", "MeanDeviation", "MedianDeviation",
            "Covariance", "Correlation", "SpearmanRho", "KendallTau",
            "PearsonCorrelationTest", "StudentTTest", "FTest",
            "ChiSquareTest", "KolmogorovSmirnovTest",
            "NormalDistribution", "UniformDistribution", "BinomialDistribution",
            "PoissonDistribution", "ExponentialDistribution",
            "GammaDistribution", "BetaDistribution", "WeibullDistribution",
            "PDF", "CDF", "InverseCDF", "RandomVariate", "RandomSample",
            "LinearModelFit", "NonlinearModelFit", "FindFit",
            "Histogram", "SmoothHistogram",
        ])
        _write_group("statistics", statistics)

        # number_theory
        number_theory = from_seeds([
            "PrimeQ", "NextPrime", "PrimePi", "Prime", "FactorInteger",
            "Divisors", "DivisorSum", "DivisorSigma", "EulerPhi",
            "MoebiusMu", "LiouvilleLambda", "JacobiSymbol", "KroneckerSymbol",
            "ChineseRemainder", "ExtendedGCD", "PowerMod",
            "MultiplicativeOrder", "PrimitiveRoot", "CarmichaelLambda",
            "IntegerDigits", "DigitCount", "IntegerExponent",
            "FromDigits", "BaseForm", "NumberForm", "NumberFieldRoots",
            "Rationalize", "ContinuedFraction", "FromContinuedFraction",
        ])
        _write_group("number_theory", number_theory)

        # special_functions
        special_functions = from_seeds([
            "Gamma", "LogGamma", "PolyGamma", "Beta", "LogBeta",
            "Erf", "Erfc", "Erfi", "InverseErf", "InverseErfc",
            "BesselJ", "BesselY", "BesselI", "BesselK",
            "AiryAi", "AiryBi", "AiryAiPrime", "AiryBiPrime",
            "HypergeometricPFQ", "Hypergeometric0F1", "Hypergeometric1F1",
            "Hypergeometric2F1", "HypergeometricU", "MeijerG",
            "LegendreP", "LegendreQ", "GegenbauerC", "ChebyshevT",
            "ChebyshevU", "HermiteH", "LaguerreL", "JacobiP",
            "SphericalHarmonicY", "ZernikeR",
            "EllipticK", "EllipticF", "EllipticE", "EllipticPi",
            "WeierstrassP", "WeierstrassPPrime",
            "Zeta", "PolyLog", "LerchPhi", "HurwitzZeta",
            "StieltjesGamma", "EulerGamma", "Catalan", "Glaisher",
            "RiemannSiegelZ", "RiemannSiegelTheta",
        ])
        _write_group("special_functions", special_functions)

        # combinatorics
        combinatorics = from_seeds([
            "Factorial", "Factorial2", "Binomial", "Multinomial",
            "Subfactorial", "Permutations", "Subsets", "Tuples",
            "IntegerPartitions", "PartitionsP", "PartitionsQ",
            "FrobeniusSolve", "StirlingS1", "StirlingS2",
            "BellB", "BernoulliB", "EulerE", "CatalanNumber",
            "Fibonacci", "LucasL", "BellY",
        ])
        _write_group("combinatorics", combinatorics)

        # list_ops
        list_ops = from_seeds([
            "List", "Range", "Table", "Array", "ConstantArray",
            "NestList", "IterationCount", "FoldList", "NestWhileList",
            "Map", "MapAt", "MapIndexed", "MapThread", "Scan",
            "Apply", "Thread", "Outer", "Inner",
            "Select", "Pick", "Cases", "DeleteCases",
            "Position", "Extract", "ReplacePart",
            "Sort", "SortBy", "Ordering", "OrderingBy",
            "Reverse", "RotateLeft", "RotateRight",
            "Flatten", "Riffle", "Partition", "Subsets",
            "Length", "Dimensions", "ArrayDepth",
            "First", "Last", "Rest", "Most", "Take", "Drop", "Part",
            "Append", "Prepend", "Insert", "Delete", "DeleteDuplicates",
            "Union", "Intersection", "Complement", "Join",
            "Tally", "Counts", "GroupBy",
            "Fold", "NestWhile", "FixedPoint", "FixedPointList",
            "Accumulate", "Differences", "Ratios", "MovingAverage",
        ])
        _write_group("list_ops", list_ops)

        # string_ops
        string_ops = from_seeds([
            "String", "StringJoin", "StringLength", "StringReverse",
            "StringTake", "StringDrop", "StringPart",
            "StringCases", "StringCount", "StringPosition",
            "StringReplace", "StringDelete", "StringInsert",
            "StringSplit", "StringRiffle",
            "StringMatchQ", "StringContainsQ", "StringStartsQ", "StringEndsQ",
            "StringFreeQ", "StringRepeat",
            "ToUpperCase", "ToLowerCase", "Capitalize",
            "Characters", "CharacterRange", "FromCharacterCode", "ToCharacterCode",
            "ToString", "ToExpression",
            "RegularExpression", "StringExpression",
            "NumberString", "DigitCharacter", "LetterCharacter",
            "WordBoundary", "StartOfString", "EndOfString",
        ])
        # ToExpression is also dangerous but belongs here contextually;
        # it will be overridden by the dynamic_eval group in filter logic
        string_ops -= {"ToExpression"}
        _write_group("string_ops", string_ops)

        # programming
        programming = from_seeds([
            "If", "Which", "Switch", "Do", "While", "For", "Goto", "Label",
            "Return", "Break", "Continue", "Throw", "Catch",
            "Module", "Block", "With", "DynamicModule",
            "Function", "Slot", "SlotSequence", "Composition",
            "Set", "SetDelayed", "Unset", "UpSet", "UpSetDelayed",
            "Rule", "RuleDelayed", "Condition",
            "OwnValues", "DownValues", "UpValues", "SubValues",
            "Clear", "ClearAll", "Remove", "Protect", "Unprotect",
            "Evaluate", "Unevaluated", "HoldForm", "HoldComplete",
            "Hold", "ReleaseHold", "Defer",
            "True", "False", "Not", "And", "Or", "Xor", "Nand", "Nor",
            "Equal", "Unequal", "Less", "Greater", "LessEqual", "GreaterEqual",
            "SameQ", "UnsameQ", "MatchQ", "FreeQ", "MemberQ",
            "Print", "Echo", "EchoFunction", "Sow", "Reap",
            "Timing", "AbsoluteTiming", "TimeConstrained",
            "MemoryConstrained", "MemoryInUse",
            "NumericQ", "IntegerQ", "EvenQ", "OddQ", "PrimeQ",
            "StringQ", "ListQ", "AtomQ", "NumberQ",
            "Head", "Length", "Depth", "LeafCount",
            "N", "Precision", "Accuracy", "SetPrecision", "SetAccuracy",
            "Infinity", "Indeterminate", "Undefined",
            "Null", "None", "Automatic", "All", "Full",
            "Association", "AssociationQ", "KeyValueMap",
            "Lookup", "KeyMemberQ", "Keys", "Values",
            "Query", "Dataset",
        ])
        _write_group("programming", programming)

        # plotting_2d
        plot2d = by_pattern("*Plot", "*Chart", "*Histogram", "ListPlot*") | from_seeds([
            "Show", "GraphicsGrid", "GraphicsRow", "GraphicsColumn",
            "Legended", "PlotLegends", "BarLegend", "LineLegend",
        ])
        plot2d = {s for s in plot2d if "3D" not in s}
        _write_group("plotting_2d", plot2d)

        # plotting_3d
        plot3d = by_pattern("*Plot3D", "*Chart3D", "ListPlot3D*", "List*3D") | from_seeds([
            "Show", "Graphics3D",
        ])
        plot3d = {s for s in plot3d if "3D" in s or s in {"Show"}}
        _write_group("plotting_3d", plot3d)

        # graphics
        graphics = from_seeds([
            "Graphics", "Graphics3D", "Rasterize", "Image",
            "Point", "Line", "Arrow", "Circle", "Disk", "Rectangle",
            "Polygon", "Triangle", "FilledCurve", "BezierCurve",
            "Sphere", "Cylinder", "Cone", "Cuboid", "Tube",
            "Text", "Inset", "Labeled", "Callout",
            "RGBColor", "Hue", "GrayLevel", "CMYKColor", "Opacity",
            "Directive", "Thick", "Thin", "Dashed", "Dotted", "Dashing",
            "Arrowheads", "AbsoluteThickness", "AbsoluteDashing",
            "EdgeForm", "FaceForm", "Style",
            "PointSize", "AbsolutePointSize",
            "GraphicsGroup", "GeometricTransformation",
            "Rotate", "Translate", "Scale", "Reflect", "Shear",
            "ImageSize", "AspectRatio", "PlotRange", "Axes", "Frame",
            "AxesLabel", "FrameLabel", "PlotLabel", "Ticks", "FrameTicks",
            "GridLines", "Background", "ColorFunction",
        ])
        _write_group("graphics", graphics)

        # ----------------------------------------------------------------
        # Dangerous groups
        # ----------------------------------------------------------------
        print("\nGenerating dangerous groups…")
        print("  Layer 1: pattern matching + seed lists")
        wld = _query_functionality_areas(session, list(all_sys))
        if wld:
            print("  Layer 2: WolframLanguageData enrichment applied")

        # system_exec
        # *Process* is too broad (catches statistical processes like ARIMAProcess).
        # Use explicit OS-level process symbols + targeted patterns instead.
        system_exec = (
            by_pattern("*Library*", "*Launch*")
            | danger_seeds([
                # OS process control
                "Run", "RunProcess", "StartProcess", "CreateProcess",
                "KillProcess", "WaitForProcess", "ProcessObject",
                "ProcessStatus", "ProcessID", "ProcessInformation",
                "ProcessConnection", "ProcessDirectory", "ProcessEnvironment",
                "SystemProcesses", "SystemProcessData",
                "RemoteRunProcess",
                # WSTP / inter-process links
                "LinkLaunch", "LinkCreate", "LinkConnect", "LinkClose",
                "LinkRead", "LinkWrite", "LinkReadHeld", "LinkWriteHeld",
                "LinkActivate", "LinkFlush", "LinkInterrupt",
                "LinkObject", "LinkOpen", "LinkReadyQ", "LinkConnectedQ",
                "Links", "ThisLink", "ParentLink",
                # Library loading
                "LibraryLoad", "LibraryUnload",
                "LibraryFunctionLoad", "LibraryFunctionUnload",
                "LibraryFunctionDeclaration",
                "CreateManagedLibraryExpression",
                "FunctionCompileExportLibrary",
                "UseEmbeddedLibrary", "FindLibrary",
                # Env / shell
                "Install", "Uninstall",
                "Environment", "SetEnvironment",
                "SystemOpen", "SystemShell", "FindProgram",
                "SystemDialogInput", "PrintDialog",
                "ReadPipe", "WritePipe",
                "NETLink", "JLink",
                "$CommandLine", "$ProcessID", "$ProcessorCount",
                "$SystemShell", "$UserName", "$MachineName",
                "$ParentLink", "$ParentProcessID",
            ])
        )
        _write_group("system_exec", system_exec | wld.get("system_exec", set()))

        # file_read
        file_read = (
            by_pattern("*Read*", "Import*", "Get", "Needs", "OpenRead", "BinaryRead*")
            | danger_seeds([
                "FileNames", "FileExistsQ", "DirectoryQ", "FileType",
                "FileDate", "FileByteCount", "FileSize", "FileInformation",
                "FindFile", "DirectoryListing", "ParentDirectory",
                "SetDirectory", "ResetDirectory",
                "$HomeDirectory", "$UserDirectory", "$RootDirectory",
                "$InitialDirectory", "$BaseDirectory",
                "$UserBaseDirectory", "$InstallationDirectory",
                "$UserDocumentsDirectory",
            ])
        )
        _write_group("file_read", file_read | wld.get("file_read", set()))

        # file_write
        file_write = (
            by_pattern("*Write*", "Export*", "Put", "PutAppend", "OpenWrite", "OpenAppend", "BinaryWrite*")
            | danger_seeds([
                "CopyFile", "RenameFile", "DeleteFile", "CreateFile",
                "CopyDirectory", "RenameDirectory",
                "CreateDirectory", "DeleteDirectory",
                "SetFileDate", "SetPermissions",
            ])
        )
        _write_group("file_write", file_write | wld.get("file_write", set()))

        # networking
        networking = (
            by_pattern("*URL*", "*HTTP*", "*Socket*", "*Mail*", "*FTP*", "*Web*", "*Fetch*")
            | danger_seeds([
                "ServiceConnect", "ServiceDisconnect", "ServiceExecute",
                "ServiceObject", "ServiceStatus",
                "CloudConnect", "CloudDisconnect",
                "CloudGet", "CloudPut", "CloudDelete",
                "CloudDeploy", "CloudSubmit", "CloudEvaluate",
                "CloudCDF", "CloudObject", "CloudObjectQ",
                "ExternalEvaluate", "StartExternalSession",
                "ChannelSend", "ChannelListen", "ChannelObject",
            ])
        )
        _write_group("networking", networking | wld.get("networking", set()))

        # dynamic_eval
        dynamic_eval = (
            by_pattern("ToExpression*")
            | danger_seeds([
                "ToExpression", "Evaluate", "MakeExpression",
                "Uncompress", "ReadProtected",
            ])
        )
        _write_group("dynamic_eval", dynamic_eval | wld.get("dynamic_eval", set()))

        # external_services
        external_services = (
            by_pattern("*Cloud*", "*Service*", "*External*", "Wolfram*")
            | danger_seeds([
                "WolframAlpha", "WolframLanguageData", "EntityValue",
                "Entity", "SemanticImport", "SemanticImportString",
                "Interpreter", "APIFunction", "FormFunction",
                "AskFunction", "AskAppend",
            ])
        )
        _write_group("external_services", external_services | wld.get("external_services", set()))

        print("\nDone. All group files written to:")
        print(f"  {GROUPS_DIR}")
