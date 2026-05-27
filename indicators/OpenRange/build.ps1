$ErrorActionPreference = "Stop"

$bookmapLib = "C:\Program Files\Bookmap\lib"
$javaBin = "C:\Program Files\Bookmap\jre\bin"
$classpath = "$bookmapLib\bm-l1api.jar;$bookmapLib\bm-simplified-api-wrapper.jar;$bookmapLib\util.jar"

# Staging output lives OUTSIDE C:\Bookmap\addons so Bookmap's recursive
# addon scan never sees build artifacts. The operator deploys explicitly
# by copying from the staging path; this script never writes to
# C:\Bookmap\addons\openrange-release.jar.
$stagingDir = 'C:\Bookmap\addons-staging\openrange'

# -Djava.awt.headless=true prevents AWT from spinning up a non-daemon
# EventQueue thread when tests touch BufferedImage / Graphics2D. Without
# this, painter tests print OK but the JVM never exits and build.ps1 hangs.
# BufferedImage off-screen rendering is fully supported in headless mode.
$jvmHeadless = @("-Djava.awt.headless=true")

function Invoke-JavaTest($className) {
    & "$javaBin\java.exe" ($jvmHeadless + @("-cp", "build\classes;build\test-classes;$classpath", $className))
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Remove-Item -Recurse -Force build\classes, build\test-classes -ErrorAction SilentlyContinue
# build\libs sits under C:\Bookmap\addons and was the original duplicate-
# jar source. Purge any prior contents so a stale jar can never linger
# inside the recursive scan tree.
if (Test-Path 'build\libs') {
    Remove-Item -Recurse -Force 'build\libs'
}
New-Item -ItemType Directory -Force -Path build\classes, build\test-classes | Out-Null
New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null

$mainSources = Get-ChildItem -Path src\main\java -Recurse -Filter *.java |
    Where-Object { $_.Name -ne "PaxOpeningRangeSimpleModule.java" } |
    ForEach-Object { $_.FullName }
& "$javaBin\javac.exe" @("-cp", $classpath, "-d", "build\classes") $mainSources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$testSources = Get-ChildItem -Path src\test\java -Recurse -Filter *.java | ForEach-Object { $_.FullName }
& "$javaBin\javac.exe" @("-cp", "build\classes;$classpath", "-d", "build\test-classes") $testSources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Invoke-JavaTest "com.openrange.PaxOpeningRangeCalculatorTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeSignalEngineTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeOrderFlowTrackerTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeSignalFormatterTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeSignalCsvLoggerTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeRollingStatsTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeSignalColorTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeCrossMarketTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeLogPathTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeRollingPercentileTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeQualityTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeFeatureCacheTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeSignalQualityGateTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeDiagnosticsTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeStateCacheTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeChartFallbackTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeModuleConcurrencyTest"
Invoke-JavaTest "com.openrange.PaxHeatwaveSnapshotParserTest"
Invoke-JavaTest "com.openrange.PaxHeatwavePainterTest"
Invoke-JavaTest "com.openrange.PaxHeatwaveFetcherTest"
Invoke-JavaTest "com.openrange.PaxHeatwaveOfflineParseTest"
Invoke-JavaTest "com.openrange.PaxHeatwaveFetcherOfflineTest"
Invoke-JavaTest "com.openrange.PaxTrendSignalSnapshotParserTest"
Invoke-JavaTest "com.openrange.PaxTrendSignalFetcherTest"
Invoke-JavaTest "com.openrange.PaxTrendTrianglePainterTest"
Invoke-JavaTest "com.openrange.PaxTrendGlyphImageTest"
Invoke-JavaTest "com.openrange.PaxNativeSignalMarkerPolicyTest"
Invoke-JavaTest "com.openrange.PaxChartTimeCoordsTest"
Invoke-JavaTest "com.openrange.PaxTrendTriangleDedupTest"
Invoke-JavaTest "com.openrange.PaxTrendSignalRuntimeStatusTest"
Invoke-JavaTest "com.openrange.PaxTrendGlyphCacheTest"
Invoke-JavaTest "com.openrange.PaxRepaintGuardTest"
Invoke-JavaTest "com.openrange.PaxOpeningRangeRenderKeyTest"
Invoke-JavaTest "com.openrange.PaxNativeMarkerGateTest"
Invoke-JavaTest "com.openrange.PaxInstitutionalSignalsHistoryTest"
Invoke-JavaTest "com.openrange.PaxInstitutionalSignalEventParseTest"
Invoke-JavaTest "com.openrange.PaxInstitutionalChartEventsHistoryTest"
Invoke-JavaTest "com.openrange.PaxInstitutionalChartEventsParseTest"
Invoke-JavaTest "com.openrange.PaxChartEventsPlumbingTest"
Invoke-JavaTest "com.openrange.PaxAiChartEventsParseTest"
Invoke-JavaTest "com.openrange.PaxAiChartEventsRenderTest"
Invoke-JavaTest "com.openrange.PaxAiChartEventsActiveHistoryTest"
Invoke-JavaTest "com.openrange.PaxTrendSignalFetcherRepaintKeyTest"
Invoke-JavaTest "com.openrange.PaxLevelEdgeSnapshotParserTest"
Invoke-JavaTest "com.openrange.PaxLevelEdgePainterTest"
Invoke-JavaTest "com.openrange.PaxChartPaletteTest"
Invoke-JavaTest "com.openrange.PaxChartEventTtlTest"
Invoke-JavaTest "com.openrange.PaxLevelEdgeMinVisibleTest"
Invoke-JavaTest "com.openrange.PaxAttackResponseGlyphTest"
Invoke-JavaTest "com.openrange.PaxAttackResponseParserAndPainterTest"
Invoke-JavaTest "com.openrange.PaxAttackResponseFetcherTest"
Invoke-JavaTest "com.openrange.PaxAttackResponseRenderWiringTest"

$jarPath = Join-Path $stagingDir 'openrange-release.jar'
if (Test-Path $jarPath) { Remove-Item -Force $jarPath }
& "$javaBin\jar.exe" @("--create", "--file", $jarPath, "-C", "build\classes", ".")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$jarItem = Get-Item $jarPath
Write-Host "Built $jarPath ($($jarItem.Length) bytes)"

# -----------------------------------------------------------------
# Hygiene invariant: Bookmap scans C:\Bookmap\addons recursively, so
# any openrange*.jar under that tree is a load candidate. The build
# must leave at most ONE such jar - the operator's canonical install
# at C:\Bookmap\addons\openrange-release.jar (if present). Anything
# else (including build\libs outputs from older script versions)
# fails the build loudly so a regression cannot silently reintroduce
# the recursive-load bug.
# -----------------------------------------------------------------
$canonical = 'C:\Bookmap\addons\openrange-release.jar'
$found = @(Get-ChildItem -Path 'C:\Bookmap\addons' -Recurse -Filter 'openrange*.jar' -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
$invalid = @($found | Where-Object { $_ -ne $canonical })
if ($invalid.Count -gt 0) {
    Write-Host ""
    Write-Host "ERROR: stray openrange*.jar under C:\Bookmap\addons (recursive scan):"
    foreach ($f in $invalid) { Write-Host "  $f" }
    Write-Host ""
    Write-Host "Bookmap scans C:\Bookmap\addons recursively. Stray jars race the canonical install."
    Write-Host "Delete the listed files or move them outside C:\Bookmap\addons before deploying."
    throw "Hygiene check failed: $($invalid.Count) stray openrange*.jar under C:\Bookmap\addons"
}

if (Test-Path $canonical) {
    $canonicalItem = Get-Item $canonical
    Write-Host "Hygiene OK: canonical $canonical present ($($canonicalItem.Length) bytes, mtime $($canonicalItem.LastWriteTime)); no stray jars under scan tree."
} else {
    Write-Host "Hygiene OK: no openrange*.jar under C:\Bookmap\addons. Canonical install absent (first deploy will create it)."
}
Write-Host "Staged jar: $jarPath"
Write-Host "Deploy step (operator, after Bookmap is closed):"
Write-Host "  Copy-Item -LiteralPath '$jarPath' -Destination '$canonical' -Force"
