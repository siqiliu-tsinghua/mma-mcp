"""Generate security group JSON files from the local Wolfram kernel.

Run at install time via:
    mma-mcp setup

This queries the local kernel for all System` symbols and categorises them
into safe / dangerous groups using WolframLanguageData FunctionalityAreas
as the primary classification source.

Strategy:
  1. Query WolframLanguageData["FunctionalityAreas"] for every System` symbol.
     Each symbol may belong to one or more FunctionalityAreas (208 distinct areas).
  2. Map each FunctionalityArea to a security group via SAFE_AREA_MAP or
     DANGEROUS_AREA_MAP.  This covers ~85% of symbols automatically.
  3. Hard-coded dangerous seeds ensure critical symbols (Run, SystemShell,
     ToExpression, etc.) are always classified even if WLD data is incomplete.
  4. Symbols with no FunctionalityArea (mostly Box/frontend internals) are
     left unclassified — the whitelist rejects them by default.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

GROUPS_DIR = Path(__file__).parent / "security" / "groups"


# ---------------------------------------------------------------------------
# FunctionalityArea → group mapping
# ---------------------------------------------------------------------------

# Safe groups: WLD areas that map to user-accessible capability groups.
SAFE_AREA_MAP: dict[str, str] = {
    # --- math_core: basic math, numeric functions ---
    "MathFunctions": "math_core",
    "BasicSymbols": "math_core",
    "OperatorSymbols": "math_core",
    "ComparisonOperatorSymbols": "math_core",
    "ArrowOperatorSymbols": "math_core",
    "VectorTeeOperatorSymbols": "math_core",
    "NumericsPrecisionSymbols": "math_core",
    "NumericSymbols": "math_core",
    "NumericConstantSymbols": "math_core",
    "InfinitySymbols": "math_core",
    "IntegerSymbols": "math_core",
    "RealSymbols": "math_core",
    "ComplexSymbols": "math_core",
    "RationalSymbols": "math_core",
    "NumberSymbols": "math_core",
    "DomainSymbols": "math_core",

    # --- algebra ---
    "SolvingSymbols": "algebra",
    "AlgebraicSymbols": "algebra",
    "AlgebraSymbols": "algebra",
    "PolynomialSymbols": "algebra",
    "RootSymbols": "algebra",
    "FiniteFieldSymbols": "algebra",
    "NumberFieldSymbols": "algebra",
    "NumberTheorySymbols": "number_theory",

    # --- calculus ---
    "CalculusSymbols": "calculus",
    "AsymptoticSymbols": "calculus",
    "DiscreteCalculusSymbols": "calculus",
    "VectorCalculusSymbols": "calculus",
    "SeriesSymbols": "calculus",
    "ContinuousFourierSymbols": "calculus",
    "DiscreteFourierSymbols": "calculus",
    "RecurrenceSymbols": "calculus",
    "ContinuedFractionSymbols": "calculus",

    # --- linear_algebra ---
    "LinearAlgebraSymbols": "linear_algebra",
    "MatrixSymbols": "linear_algebra",
    "ArraySymbols": "linear_algebra",
    "TensorSymbols": "linear_algebra",

    # --- statistics ---
    "StatisticsSymbols": "statistics",
    "StatisticalDistributionSymbols": "statistics",
    "StatisticalTestSymbols": "statistics",
    "StatisticalProcessSymbols": "statistics",
    "SpatialStatisticsSymbols": "statistics",
    "ClusteringSymbols": "statistics",
    "FittingSymbols": "statistics",
    "TimeSeriesSymbols": "statistics",
    "UncertaintySymbols": "statistics",

    # --- combinatorics ---
    "CombinatorSymbols": "combinatorics",
    "PermutationSymbols": "combinatorics",
    "GroupTheorySymbols": "combinatorics",
    "NamedGroupSymbols": "combinatorics",
    "GameTheorySymbols": "combinatorics",

    # --- data_structures: lists, strings, associations ---
    "ListSymbols": "data_structures",
    "AssociationSymbols": "data_structures",
    "TreeSymbols": "data_structures",
    "DatasetSymbols": "data_structures",
    "TabularSymbols": "data_structures",
    "StringSymbols": "data_structures",
    "SetSymbols": "data_structures",
    "SortingSymbols": "data_structures",
    "RestructuringSymbols": "data_structures",
    "SearchSymbols": "data_structures",
    "MinMaxSymbols": "data_structures",
    "ExtractionSymbols": "data_structures",
    "TextStringSymbols": "data_structures",
    "GrammarSymbols": "data_structures",
    "BinaryDataSymbols": "data_structures",
    "BitSymbols": "data_structures",

    # --- programming: control flow, patterns, logic ---
    "CodeFlowSymbols": "programming",
    "PatternSymbols": "programming",
    "LogicSymbols": "programming",
    "BooleanSymbols": "programming",
    "FunctionSymbols": "programming",
    "RuleSymbols": "programming",
    "IntervalSymbols": "programming",
    "AnnotationSymbols": "programming",
    "ExpressionTestingSymbols": "programming",
    "SymbolInformationSymbols": "programming",
    "SymbolAssignmentSymbols": "programming",
    "SymbolContextSymbols": "programming",
    "SymbolValueSymbols": "programming",
    "StructuralSymbols": "programming",
    "RawExpressionSymbols": "programming",
    "ExpressionSizeSymbols": "programming",
    "TimeMemorySymbols": "programming",
    "MessagesAndPrintingSymbols": "programming",
    "TestSymbols": "programming",
    "SpecialSymbols": "programming",
    "Symbols": "programming",

    # --- visualization: 2D/3D plots, charts, graphics ---
    "PlottingSymbols": "visualization",
    "ChartSymbols": "visualization",
    "HistogramSymbols": "visualization",
    "GaugeSymbols": "visualization",
    "LegendSymbols": "visualization",
    "GraphicsSymbols": "visualization",
    "GraphicsPrimitiveSymbols": "visualization",
    "ColorSymbols": "visualization",
    "StyleSymbols": "visualization",
    "GeometricTransformSymbols": "visualization",
    "FormSymbols": "visualization",
    "FormattingSymbols": "visualization",
    "AlignmentSymbols": "visualization",
    "FontSymbols": "visualization",

    # --- graph_theory ---
    "GraphTheorySymbols": "graph_theory",
    "GraphBooleanPropertySymbols": "graph_theory",
    "GraphDistributionSymbols": "graph_theory",
    "NamedGraphSymbols": "graph_theory",

    # --- geometry ---
    "RegionSymbols": "geometry",
    "GeometricSceneSymbols": "geometry",
    "AngleSymbols": "geometry",
    "GeodesySymbols": "geometry",
    "GeoGraphicsSymbols": "geometry",
    "GeoGraphicsPrimitiveSymbols": "geometry",
    "AstronomySymbols": "geometry",
    "AstroGraphicsSymbols": "geometry",

    # --- optimization ---
    "OptimizationSymbols": "optimization",
    "PDEModelSymbols": "optimization",
    "ControlSystemSymbols": "optimization",
    "ControlObjectSymbols": "optimization",
    "ControlObjectOptions": "optimization",
    "SystemModelSymbols": "optimization",

    # --- signal_processing ---
    "SignalProcessingSymbols": "signal_processing",
    "WaveletSymbols": "signal_processing",
    "AudioSymbols": "signal_processing",
    "VideoSymbols": "signal_processing",
    "SoundSymbols": "signal_processing",

    # --- image ---
    "ImageSymbols": "image",
    "ImageFilterSymbols": "image",

    # --- machine_learning ---
    "MachineLearningSymbols": "machine_learning",
    "NetSymbols": "machine_learning",
    "LLMSymbols": "machine_learning",
    "VectorDatabaseSymbols": "machine_learning",

    # --- chemistry_biology ---
    "MoleculeSymbols": "chemistry_biology",
    "BioSequenceSymbols": "chemistry_biology",

    # --- quantitative ---
    "QuantitySymbols": "quantitative",
    "QuantityVariableSymbols": "quantitative",
    "DateSymbols": "quantitative",
    "FinanceSymbols": "quantitative",
    "TravelSymbols": "quantitative",

    # --- compile ---
    "CompileSymbols": "compile",
    "ParallelSymbols": "compile",
    "CodeActionSymbols": "compile",

    # --- crypto ---
    "CryptographySymbols": "crypto",

    # --- fractal ---
    "FractalSymbols": "fractal",
    "DiffSymbols": "fractal",

    # --- interpolation ---
    "InterpolationSymbols": "interpolation",

    # --- misc safe areas ---
    "CounterSymbols": "programming",
    "TemplateSymbols": "programming",
    "MathFunctionWindows": "math_core",
}

# Dangerous groups: WLD areas that map to restricted capability groups.
DANGEROUS_AREA_MAP: dict[str, str] = {
    # --- system_exec ---
    "ExternalProcessSymbols": "system_exec",
    "LibraryLinkSymbols": "system_exec",
    "LinkSymbols": "system_exec",
    "DeviceSymbols": "system_exec",
    "EnvironmentSymbols": "system_exec",
    "SystemCredentialSymbols": "system_exec",
    "DialogSymbols": "system_exec",
    "ScheduledTaskSymbols": "system_exec",
    "MachineSymbols": "system_exec",
    "TaskSymbols": "system_exec",

    # --- file_read ---
    "FileSystemSymbols": "file_read",  # further split by name (write-flavored → file_write)
    "DirectorySymbols": "file_read",
    "StreamSymbols": "file_read",
    "PackageSymbols": "file_read",

    # --- file_write ---
    "PersistentObjectSymbols": "file_write",
    "LocalObjectSymbols": "file_write",

    # --- networking ---
    "URLSymbols": "networking",
    "SocketSymbols": "networking",
    "ChannelSymbols": "networking",
    "WebSessionSymbols": "networking",
    "PacketSymbols": "networking",

    # --- dynamic_eval ---
    "CodeEvaluationSymbols": "dynamic_eval",
    "DynamicSymbols": "dynamic_eval",
    "CodeInterruptionSymbols": "dynamic_eval",

    # --- external_services ---
    "CloudSymbols": "external_services",
    "ServiceSymbols": "external_services",
    "ExternalSessionSymbols": "external_services",
    "BlockchainSymbols": "external_services",
    "WolframAlphaSymbols": "external_services",
    "DatabinSymbols": "external_services",
    "ExternalStorageSymbols": "external_services",
    "ResourceSymbols": "external_services",
    "EntitySymbols": "external_services",
    "InterpreterSymbols": "external_services",
    "AskSymbols": "external_services",
    "AsynchronousTaskSymbols": "external_services",
}

# FunctionalityAreas we intentionally skip (frontend-only, not relevant to MCP)
SKIP_AREAS = {
    "CellSymbols", "FrontEndSymbols", "FrontEndOptions", "NotebookSymbols",
    "BoxSymbols", "BoxOptions", "ButtonSymbols", "SliderSymbols",
    "AnimatorElements", "MenuSymbols", "PageSymbols", "PaletteSymbols",
    "ControllerSymbols", "ViewerSymbols", "GlobalOptions",
    "AutocompleteSymbols", "OptionSymbols", "ModuleSymbols", "CDFSymbols",
    "InitializationSymbols", "DebugSymbols", "PacletSymbols",
    "PacletManagerSymbols", "AssessmentSymbols",
    "FrontEndExecutionSymbols",
}

# Keywords that mark a FileSystemSymbols member as write-flavoured.
_WRITE_KEYWORDS = ("Write", "Export", "Put", "Save", "Copy", "Rename",
                    "Delete", "Create", "Set", "Move")

# ---------------------------------------------------------------------------
# Safe seeds — symbols that WLD classifies as PacletSymbols (skipped) but
# should be in safe groups for data_query tool support.
# ---------------------------------------------------------------------------

SAFE_SEEDS: dict[str, list[str]] = {
    "quantitative": [
        # Curated data functions — bundled with Wolfram Engine, work offline
        "CountryData", "CityData", "ElementData", "ChemicalData",
        "PlanetData", "StarData", "UnitConvert",
        "MovieData", "WordData", "GenomeData",
        "PolyhedronData", "KnotData", "GraphData",
        "IsotopeData", "MineralData", "SatelliteData",
        "AircraftData", "FoodData",
    ],
}

# ---------------------------------------------------------------------------
# Hard-coded dangerous seeds — always included regardless of WLD data.
# These are the most critical symbols that MUST be in dangerous groups
# even if WLD classification is incomplete or unavailable.
# ---------------------------------------------------------------------------

DANGEROUS_SEEDS: dict[str, list[str]] = {
    "system_exec": [
        # OS process control
        "Run", "RunProcess", "StartProcess", "CreateProcess",
        "KillProcess", "WaitForProcess", "ProcessObject",
        "ProcessStatus", "ProcessID", "ProcessInformation",
        "ProcessConnection", "ProcessDirectory", "ProcessEnvironment",
        "SystemProcesses", "SystemProcessData", "RemoteRunProcess",
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
    ],
    "file_read": [
        "FileNames", "FileExistsQ", "DirectoryQ", "FileType",
        "FileDate", "FileByteCount", "FileSize", "FileInformation",
        "FindFile", "DirectoryListing", "ParentDirectory",
        "SetDirectory", "ResetDirectory",
        "Import", "Get", "Needs", "OpenRead",
        "Read", "ReadString", "ReadList", "ReadLine", "ReadByteArray",
        "BinaryRead", "BinaryReadList",
        "$HomeDirectory", "$UserDirectory", "$RootDirectory",
        "$InitialDirectory", "$BaseDirectory",
        "$UserBaseDirectory", "$InstallationDirectory",
        "$UserDocumentsDirectory",
    ],
    "file_write": [
        "Export", "Put", "PutAppend", "OpenWrite", "OpenAppend",
        "Write", "WriteString", "WriteLine", "BinaryWrite",
        "CopyFile", "RenameFile", "DeleteFile", "CreateFile",
        "CopyDirectory", "RenameDirectory",
        "CreateDirectory", "DeleteDirectory",
        "SetFileDate", "SetPermissions",
    ],
    "networking": [
        "URLRead", "URLFetch", "URLExecute", "URLSubmit",
        "HTTPRequest", "HTTPResponse",
        "SocketConnect", "SocketListen", "SocketOpen",
        "SendMail", "MailReceiverFunction",
        "ServiceConnect", "ServiceDisconnect", "ServiceExecute",
        "ServiceObject", "ServiceStatus",
        "ChannelSend", "ChannelListen", "ChannelObject",
        "WebExecute", "WebSearch",
    ],
    "dynamic_eval": [
        "ToExpression", "Evaluate", "MakeExpression",
        "Uncompress", "ReadProtected",
    ],
    "external_services": [
        "CloudConnect", "CloudDisconnect",
        "CloudGet", "CloudPut", "CloudDelete",
        "CloudDeploy", "CloudSubmit", "CloudEvaluate",
        "CloudObject", "CloudObjectQ",
        "ExternalEvaluate", "StartExternalSession",
        "WolframAlpha", "WolframLanguageData",
        "EntityValue", "Entity",
        "SemanticImport", "SemanticImportString",
        "Interpreter", "APIFunction", "FormFunction",
        "AskFunction", "AskAppend",
        # Live data functions that fetch from the internet
        "FinancialData", "WeatherData",
    ],
}


# ---------------------------------------------------------------------------
# Manifest template
# ---------------------------------------------------------------------------

MANIFEST: dict[str, dict] = {
    "groups": {
        # Safe groups (22)
        "math_core":          {"description": "Basic math, numeric functions, constants, operators", "dangerous": False},
        "algebra":            {"description": "Algebraic manipulation: Solve, Factor, Expand, Simplify", "dangerous": False},
        "calculus":           {"description": "Differentiation, integration, limits, series, transforms", "dangerous": False},
        "linear_algebra":     {"description": "Matrix and vector operations, decompositions", "dangerous": False},
        "statistics":         {"description": "Descriptive statistics, distributions, hypothesis tests, fitting", "dangerous": False},
        "number_theory":      {"description": "Prime testing, factorization, modular arithmetic", "dangerous": False},
        "combinatorics":      {"description": "Permutations, subsets, group theory, game theory", "dangerous": False},
        "data_structures":    {"description": "Lists, strings, associations, trees, datasets, sets", "dangerous": False},
        "programming":        {"description": "Control flow, patterns, logic, scoping, testing", "dangerous": False},
        "visualization":      {"description": "2D/3D plots, charts, graphics primitives, styling", "dangerous": False},
        "graph_theory":       {"description": "Graph construction, properties, named graphs", "dangerous": False},
        "geometry":           {"description": "Regions, geometric scenes, geodesy, geo-graphics", "dangerous": False},
        "optimization":       {"description": "Optimization, PDE models, control systems", "dangerous": False},
        "signal_processing":  {"description": "Signal processing, wavelets, audio, video", "dangerous": False},
        "image":              {"description": "Image processing and filtering", "dangerous": False},
        "machine_learning":   {"description": "Machine learning, neural networks, LLM, vector databases", "dangerous": False},
        "chemistry_biology":  {"description": "Molecules, bio-sequences", "dangerous": False},
        "quantitative":       {"description": "Quantities, units, dates, finance, travel", "dangerous": False},
        "compile":            {"description": "Compilation, parallel computing, code actions", "dangerous": False},
        "crypto":             {"description": "Cryptographic functions", "dangerous": False},
        "fractal":            {"description": "Fractals and diff operations", "dangerous": False},
        "interpolation":      {"description": "Interpolation functions", "dangerous": False},
        # Dangerous groups (6)
        "file_read":          {"description": "Read files from the local filesystem", "dangerous": True},
        "file_write":         {"description": "Write or delete files on the local filesystem", "dangerous": True},
        "networking":         {"description": "Fetch external URLs, open sockets, send mail", "dangerous": True},
        "system_exec":        {"description": "Execute OS commands, load libraries, manage processes", "dangerous": True},
        "dynamic_eval":       {"description": "Dynamically construct and evaluate arbitrary code", "dangerous": True},
        "external_services":  {"description": "Connect to Wolfram Cloud and third-party services", "dangerous": True},
    }
}


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


def _write_manifest() -> None:
    path = GROUPS_DIR / "manifest.json"
    path.write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  manifest.json  ({len(MANIFEST['groups'])} groups)")


# ---------------------------------------------------------------------------
# WolframLanguageData batch query
# ---------------------------------------------------------------------------

def _query_all_areas(
    session,
    all_sys: list[str],
    batch_size: int = 100,
) -> dict[str, set[str]]:
    """Query WolframLanguageData FunctionalityAreas for all symbols.

    Returns {symbol_short_name: {area1, area2, ...}}.
    Symbols with no area are omitted.
    """
    import time
    from wolframclient.language import wlexpr

    sym_areas: dict[str, set[str]] = {}
    total = len(all_sys)
    n_batches = (total + batch_size - 1) // batch_size
    t_start = time.time()
    failed_batches = 0
    total_assignments = 0

    print(f"  Querying WolframLanguageData for {total} symbols "
          f"({n_batches} batches of {batch_size})...", flush=True)

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
                    sym_areas.setdefault(sym, set()).add(area_str)
                    batch_found += 1

        total_assignments += batch_found
        pct = batch_num * 100 // n_batches
        print(f"    batch {batch_num}/{n_batches} ({pct}%) "
              f"+{batch_found} areas ({elapsed:.1f}s)", flush=True)

    total_time = time.time() - t_start
    status = "partial" if failed_batches else "complete"
    print(f"  Done ({status}): {len(sym_areas)} symbols with areas, "
          f"{total_assignments} total assignments "
          f"({failed_batches} batches failed, {total_time:.0f}s total)\n", flush=True)

    return sym_areas


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _classify_symbols(
    sym_areas: dict[str, set[str]],
    all_sys: set[str],
) -> dict[str, set[str]]:
    """Classify symbols into groups based on FunctionalityAreas + seeds.

    Returns {group_name: {symbol1, symbol2, ...}}.
    """
    groups: dict[str, set[str]] = {}

    # Initialize all groups from manifest
    for group_name in MANIFEST["groups"]:
        groups[group_name] = set()

    # Classify by WLD FunctionalityAreas
    unmapped_areas: set[str] = set()
    for sym, areas in sym_areas.items():
        short = _short(sym)
        for area in areas:
            if area in SKIP_AREAS:
                continue
            elif area in SAFE_AREA_MAP:
                groups[SAFE_AREA_MAP[area]].add(short)
            elif area in DANGEROUS_AREA_MAP:
                target = DANGEROUS_AREA_MAP[area]
                # FileSystemSymbols: split by write-flavoured keywords
                if area == "FileSystemSymbols":
                    if any(kw in short for kw in _WRITE_KEYWORDS):
                        groups["file_write"].add(short)
                    else:
                        groups["file_read"].add(short)
                else:
                    groups[target].add(short)
            else:
                unmapped_areas.add(area)

    if unmapped_areas:
        print(f"  Warning: {len(unmapped_areas)} unmapped FunctionalityAreas: "
              f"{sorted(unmapped_areas)[:10]}{'...' if len(unmapped_areas) > 10 else ''}")

    # Add safe seeds (symbols WLD classifies as PacletSymbols but we need)
    for group_name, seeds in SAFE_SEEDS.items():
        groups[group_name] |= set(seeds)

    # Add hard-coded dangerous seeds (always, regardless of WLD)
    for group_name, seeds in DANGEROUS_SEEDS.items():
        groups[group_name] |= set(seeds)

    # Ensure dangerous symbols are NOT in any safe group
    all_dangerous: set[str] = set()
    for group_name, meta in MANIFEST["groups"].items():
        if meta["dangerous"]:
            all_dangerous |= groups[group_name]

    for group_name, meta in MANIFEST["groups"].items():
        if not meta["dangerous"]:
            overlap = groups[group_name] & all_dangerous
            if overlap:
                groups[group_name] -= overlap
                logger.debug(
                    "Removed %d dangerous symbols from safe group %s",
                    len(overlap), group_name,
                )

    return groups


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
        print("Warning: could not auto-detect kernel, letting wolframclient try...")

    print("Starting Wolfram kernel...")
    with WolframLanguageSession(kernel=resolved) as session:
        print("Kernel ready.\n")

        # --- All System` symbols (ground truth for this kernel version) ---
        raw_names = session.evaluate(wlexpr('Names["System`*"]'))
        all_sys_full = list(raw_names)
        all_sys_short: set[str] = {_short(s) for s in all_sys_full}
        print(f"Total System` symbols: {len(all_sys_short)}\n")

        # --- Query WolframLanguageData ---
        print("Querying FunctionalityAreas...")
        sym_areas = _query_all_areas(session, [_short(s) for s in all_sys_full])

        # --- Classify ---
        print("Classifying symbols...")
        groups = _classify_symbols(sym_areas, all_sys_short)

        # --- Remove old group files ---
        for old_file in GROUPS_DIR.glob("*.json"):
            if old_file.stem != "manifest":
                old_file.unlink()

        # --- Write group files ---
        print("\nWriting group files...")
        total_safe = 0
        total_dangerous = 0
        for group_name in sorted(MANIFEST["groups"]):
            syms = groups.get(group_name, set())
            if syms:
                _write_group(group_name, syms)
                if MANIFEST["groups"][group_name]["dangerous"]:
                    total_dangerous += len(syms)
                else:
                    total_safe += len(syms)

        _write_manifest()

        # --- Summary ---
        safe_groups = [g for g, m in MANIFEST["groups"].items() if not m["dangerous"]]
        danger_groups = [g for g, m in MANIFEST["groups"].items() if m["dangerous"]]
        classified = set()
        for syms in groups.values():
            classified |= syms
        unclassified = all_sys_short - classified

        print(f"\nSummary:")
        print(f"  Safe groups:      {len(safe_groups)} ({total_safe} symbols)")
        print(f"  Dangerous groups: {len(danger_groups)} ({total_dangerous} symbols)")
        print(f"  Classified:       {len(classified)} / {len(all_sys_short)} symbols")
        print(f"  Unclassified:     {len(unclassified)} symbols")

        if unclassified and len(unclassified) < 50:
            print(f"  Unclassified: {sorted(unclassified)}")

        print(f"\nDone. All group files written to:")
        print(f"  {GROUPS_DIR}")
