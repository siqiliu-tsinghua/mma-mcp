#!/usr/bin/env wolframscript
(* generate_groups.wl
   Regenerates ALL security group JSON files from the local Wolfram kernel.

   Usage (from project root):
       wolframscript -file scripts/generate_groups.wl

   Alternatively, use the built-in Python command (recommended):
       mma-mcp setup

   This script is the WL-native equivalent of src/mma_mcp/setup_groups.py.
   Both produce identical output.  Re-run after a Wolfram Engine upgrade.
*)

groupsDir = FileNameJoin[{DirectoryName[$InputFileName], "..",
  "src", "mma_mcp", "security", "groups"}];

writeGroup[name_String, symbols_List] :=
  Module[{path = FileNameJoin[{groupsDir, name <> ".json"}]},
    Export[path, Union[symbols], "JSON"];
    Print["  ", name, ".json  (", Length[Union[symbols]], " symbols)"];
  ];

(* --- Ground truth: all System` short names in this kernel version --- *)
allSys = Last[StringSplit[#, "`"]] & /@ Names["System`*"];
hasKW[s_String, kws_List] := AnyTrue[kws, StringContainsQ[s, #] &];
byPat[pats___] := Union @@ (
  Last[StringSplit[#, "`"]] & /@ Names["System`" <> #] & /@ {pats}
);
seeds[ss_List] := Select[ss, MemberQ[allSys, #] &];

Print["Total System` symbols: ", Length[allSys]];
Print[""];
Print["Safe groups..."];

(* arithmetic *)
arithRaw = Select[Names["System`*"],
  MemberQ[Attributes[ToExpression[#]], NumericFunction | Listable] &];
arithRaw = Last[StringSplit[#, "`"]] & /@ arithRaw;
arithmetic = Union[arithRaw, seeds[{
  "Pi","E","I","Infinity","DirectedInfinity","Degree",
  "Sin","Cos","Tan","Cot","Sec","Csc",
  "ArcSin","ArcCos","ArcTan","ArcCot","ArcSec","ArcCsc",
  "Sinh","Cosh","Tanh","ArcSinh","ArcCosh","ArcTanh",
  "Log","Log2","Log10","Exp","Sqrt","CubeRoot",
  "Abs","Sign","Floor","Ceiling","Round",
  "IntegerPart","FractionalPart","Mod","Quotient",
  "GCD","LCM","Max","Min","Total",
  "Re","Im","Conjugate","Arg"
}]];
writeGroup["arithmetic", arithmetic];

writeGroup["algebra", seeds[{
  "Solve","NSolve","Reduce","FindRoot","Factor","Expand","ExpandAll",
  "ExpandNumerator","ExpandDenominator","Collect","Cancel","Together","Apart",
  "PolynomialGCD","PolynomialLCM","PolynomialQuotient","PolynomialRemainder",
  "PolynomialReduce","PolynomialMod","Roots","NRoots",
  "TrigExpand","TrigFactor","TrigReduce","TrigToExp","ExpToTrig",
  "Simplify","FullSimplify","Refine","FunctionExpand","PowerExpand",
  "ComplexExpand","RootReduce","ToRadicals","Decompose","Discriminant",
  "Resultant","GroebnerBasis",
  "Numerator","Denominator","Coefficient","CoefficientList",
  "CoefficientRules","Exponent","Variables","MonomialList"
}]];

writeGroup["calculus", seeds[{
  "D","Dt","Derivative","Integrate","NIntegrate",
  "DSolve","NDSolve","NDSolveValue","ParametricNDSolve",
  "Limit","Series","SeriesCoefficient","Normal","Residue",
  "LaplaceTransform","InverseLaplaceTransform",
  "FourierTransform","InverseFourierTransform",
  "ZTransform","InverseZTransform",
  "Sum","NSum","Product","NProduct",
  "Asymptotic","AsymptoticIntegrate","AsymptoticDSolveValue"
}]];

writeGroup["linear_algebra", seeds[{
  "LinearSolve","NullSpace","RowReduce","MatrixRank",
  "Det","Inverse","Transpose","ConjugateTranspose",
  "Dot","Cross","Norm","Normalize",
  "Eigenvalues","Eigenvectors","Eigensystem",
  "SingularValueDecomposition","LUDecomposition",
  "CholeskyDecomposition","QRDecomposition",
  "SchurDecomposition","HessenbergDecomposition",
  "MatrixExp","MatrixLog","MatrixPower","MatrixFunction",
  "KroneckerProduct","TensorProduct","Tr",
  "DiagonalMatrix","IdentityMatrix","HilbertMatrix",
  "VandermondeMatrix","ToeplitzMatrix","HadamardMatrix"
}]];

writeGroup["statistics", seeds[{
  "Mean","Median","Variance","StandardDeviation","Skewness","Kurtosis",
  "Quantile","Quartiles","InterquartileRange","MeanDeviation","MedianDeviation",
  "Covariance","Correlation","SpearmanRho","KendallTau",
  "PearsonCorrelationTest","StudentTTest","FTest","ChiSquareTest","KolmogorovSmirnovTest",
  "NormalDistribution","UniformDistribution","BinomialDistribution",
  "PoissonDistribution","ExponentialDistribution",
  "GammaDistribution","BetaDistribution","WeibullDistribution",
  "PDF","CDF","InverseCDF","RandomVariate","RandomSample",
  "LinearModelFit","NonlinearModelFit","FindFit",
  "Histogram","SmoothHistogram"
}]];

writeGroup["number_theory", seeds[{
  "PrimeQ","NextPrime","PrimePi","Prime","FactorInteger",
  "Divisors","DivisorSum","DivisorSigma","EulerPhi",
  "MoebiusMu","LiouvilleLambda","JacobiSymbol","KroneckerSymbol",
  "ChineseRemainder","ExtendedGCD","PowerMod",
  "MultiplicativeOrder","PrimitiveRoot","CarmichaelLambda",
  "IntegerDigits","DigitCount","IntegerExponent",
  "FromDigits","BaseForm","NumberForm","Rationalize",
  "ContinuedFraction","FromContinuedFraction"
}]];

writeGroup["special_functions", seeds[{
  "Gamma","LogGamma","PolyGamma","Beta","LogBeta",
  "Erf","Erfc","Erfi","InverseErf","InverseErfc",
  "BesselJ","BesselY","BesselI","BesselK",
  "AiryAi","AiryBi","AiryAiPrime","AiryBiPrime",
  "HypergeometricPFQ","Hypergeometric0F1","Hypergeometric1F1",
  "Hypergeometric2F1","HypergeometricU","MeijerG",
  "LegendreP","LegendreQ","GegenbauerC","ChebyshevT","ChebyshevU",
  "HermiteH","LaguerreL","JacobiP","SphericalHarmonicY","ZernikeR",
  "EllipticK","EllipticF","EllipticE","EllipticPi",
  "WeierstrassP","WeierstrassPPrime",
  "Zeta","PolyLog","LerchPhi","HurwitzZeta",
  "StieltjesGamma","EulerGamma","Catalan","Glaisher",
  "RiemannSiegelZ","RiemannSiegelTheta"
}]];

writeGroup["combinatorics", seeds[{
  "Factorial","Factorial2","Binomial","Multinomial","Subfactorial",
  "Permutations","Subsets","Tuples","IntegerPartitions",
  "PartitionsP","PartitionsQ","FrobeniusSolve",
  "StirlingS1","StirlingS2","BellB","BernoulliB","EulerE",
  "CatalanNumber","Fibonacci","LucasL","BellY"
}]];

writeGroup["list_ops", seeds[{
  "List","Range","Table","Array","ConstantArray",
  "NestList","FoldList","NestWhileList",
  "Map","MapAt","MapIndexed","MapThread","Scan",
  "Apply","Thread","Outer","Inner",
  "Select","Pick","Cases","DeleteCases",
  "Position","Extract","ReplacePart",
  "Sort","SortBy","Ordering","OrderingBy",
  "Reverse","RotateLeft","RotateRight",
  "Flatten","Riffle","Partition",
  "Length","Dimensions","ArrayDepth",
  "First","Last","Rest","Most","Take","Drop","Part",
  "Append","Prepend","Insert","Delete","DeleteDuplicates",
  "Union","Intersection","Complement","Join",
  "Tally","Counts","GroupBy",
  "Fold","NestWhile","FixedPoint","FixedPointList",
  "Accumulate","Differences","Ratios","MovingAverage"
}]];

writeGroup["string_ops", seeds[{
  "String","StringJoin","StringLength","StringReverse",
  "StringTake","StringDrop","StringPart",
  "StringCases","StringCount","StringPosition",
  "StringReplace","StringDelete","StringInsert",
  "StringSplit","StringRiffle",
  "StringMatchQ","StringContainsQ","StringStartsQ","StringEndsQ",
  "StringFreeQ","StringRepeat",
  "ToUpperCase","ToLowerCase","Capitalize",
  "Characters","CharacterRange","FromCharacterCode","ToCharacterCode",
  "ToString","RegularExpression","StringExpression",
  "NumberString","DigitCharacter","LetterCharacter",
  "WordBoundary","StartOfString","EndOfString"
}]];

writeGroup["programming", seeds[{
  "If","Which","Switch","Do","While","For","Goto","Label",
  "Return","Break","Continue","Throw","Catch",
  "Module","Block","With",
  "Function","Slot","SlotSequence","Composition",
  "Set","SetDelayed","Unset","UpSet","UpSetDelayed",
  "Rule","RuleDelayed","Condition",
  "OwnValues","DownValues","UpValues","SubValues",
  "Clear","ClearAll","Remove","Protect","Unprotect",
  "Evaluate","Unevaluated","HoldForm","HoldComplete",
  "Hold","ReleaseHold","Defer",
  "True","False","Not","And","Or","Xor","Nand","Nor",
  "Equal","Unequal","Less","Greater","LessEqual","GreaterEqual",
  "SameQ","UnsameQ","MatchQ","FreeQ","MemberQ",
  "Print","Echo","EchoFunction","Sow","Reap",
  "Timing","AbsoluteTiming","TimeConstrained","MemoryConstrained",
  "NumericQ","IntegerQ","EvenQ","OddQ","StringQ","ListQ","AtomQ","NumberQ",
  "Head","Length","Depth","LeafCount",
  "N","Precision","Accuracy","SetPrecision","SetAccuracy",
  "Null","None","Automatic","All","Full",
  "Association","AssociationQ","KeyValueMap",
  "Lookup","KeyMemberQ","Keys","Values"
}]];

plot2d = Select[byPat["*Plot","*Chart","*Histogram","ListPlot*"],
  !StringContainsQ[#,"3D"] &];
writeGroup["plotting_2d", Union[plot2d,
  seeds[{"Show","GraphicsGrid","GraphicsRow","GraphicsColumn","Legended"}]]];

plot3d = Select[byPat["*Plot3D","*Chart3D","ListPlot3D","List*3D"],
  StringContainsQ[#,"3D"] || #=="Show" &];
writeGroup["plotting_3d", Union[plot3d, seeds[{"Show","Graphics3D"}]]];

writeGroup["graphics", seeds[{
  "Graphics","Graphics3D","Rasterize","Image",
  "Point","Line","Arrow","Circle","Disk","Rectangle",
  "Polygon","Triangle","FilledCurve","BezierCurve",
  "Sphere","Cylinder","Cone","Cuboid","Tube",
  "Text","Inset","Labeled","Callout",
  "RGBColor","Hue","GrayLevel","CMYKColor","Opacity",
  "Directive","Thick","Thin","Dashed","Dotted","Dashing",
  "Arrowheads","AbsoluteThickness","AbsoluteDashing",
  "EdgeForm","FaceForm","Style","PointSize","AbsolutePointSize",
  "GraphicsGroup","GeometricTransformation",
  "Rotate","Translate","Scale","Reflect","Shear",
  "ImageSize","AspectRatio","PlotRange","Axes","Frame",
  "AxesLabel","FrameLabel","PlotLabel","Ticks","FrameTicks",
  "GridLines","Background","ColorFunction"
}]];

Print[""];
Print["Dangerous groups..."];

writeGroup["system_exec", Union[
  byPat["*Process*","*Library*","*Link*","Run","RunProcess",
        "StartProcess","CreateProcess","Install","Uninstall","*Launch*"],
  seeds[{
    "Environment","SetEnvironment","SystemOpen","FindProgram",
    "SystemDialogInput","PrintDialog","ReadPipe","WritePipe",
    "NETLink","JLink",
    "$CommandLine","$ProcessID","$ProcessorCount",
    "$SystemShell","$UserName","$MachineName"
  }]
]];

writeGroup["file_read", Union[
  byPat["*Read*","Import*","OpenRead","BinaryRead*"],
  seeds[{
    "Get","Needs",
    "FileNames","FileExistsQ","DirectoryQ","FileType",
    "FileDate","FileByteCount","FileSize","FileInformation",
    "FindFile","DirectoryListing","ParentDirectory",
    "$HomeDirectory","$UserDirectory","$RootDirectory",
    "$InitialDirectory","$BaseDirectory",
    "$UserBaseDirectory","$InstallationDirectory","$UserDocumentsDirectory"
  }]
]];

writeGroup["file_write", Union[
  byPat["*Write*","Export*","OpenWrite","OpenAppend","BinaryWrite*"],
  seeds[{
    "Put","PutAppend",
    "CopyFile","RenameFile","DeleteFile","CreateFile",
    "CopyDirectory","RenameDirectory","CreateDirectory","DeleteDirectory",
    "SetFileDate","SetPermissions"
  }]
]];

writeGroup["networking", Union[
  byPat["*URL*","*HTTP*","*Socket*","*Mail*","*FTP*","*Web*","*Fetch*"],
  seeds[{
    "ServiceConnect","ServiceDisconnect","ServiceExecute",
    "ServiceObject","ServiceStatus",
    "CloudConnect","CloudDisconnect",
    "CloudGet","CloudPut","CloudDelete",
    "CloudDeploy","CloudSubmit","CloudEvaluate",
    "CloudCDF","CloudObject","CloudObjectQ",
    "ExternalEvaluate","StartExternalSession",
    "ChannelSend","ChannelListen","ChannelObject"
  }]
]];

writeGroup["dynamic_eval", Union[
  byPat["ToExpression*"],
  seeds[{"ToExpression","Uncompress","ReadProtected"}]
]];

writeGroup["external_services", Union[
  byPat["*Cloud*","*Service*","*External*","Wolfram*"],
  seeds[{
    "WolframAlpha","WolframLanguageData","EntityValue",
    "Entity","SemanticImport","SemanticImportString",
    "Interpreter","APIFunction","FormFunction"
  }]
]];

Print[""];
Print["Done. Files written to: ", groupsDir];
