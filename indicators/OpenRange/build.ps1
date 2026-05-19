$ErrorActionPreference = "Stop"

$bookmapLib = "C:\Program Files\Bookmap\lib"
$javaBin = "C:\Program Files\Bookmap\jre\bin"
$classpath = "$bookmapLib\bm-l1api.jar;$bookmapLib\bm-simplified-api-wrapper.jar;$bookmapLib\util.jar"

# -Djava.awt.headless=true prevents AWT from spinning up a non-daemon
# EventQueue thread when tests touch BufferedImage / Graphics2D. Without
# this, painter tests print OK but the JVM never exits and build.ps1 hangs.
# BufferedImage off-screen rendering is fully supported in headless mode.
$jvmHeadless = @("-Djava.awt.headless=true")

function Invoke-JavaTest($className) {
    & "$javaBin\java.exe" ($jvmHeadless + @("-cp", "build\classes;build\test-classes;$classpath", $className))
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Remove-Item -Recurse -Force build\classes, build\test-classes, build\libs -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path build\classes, build\test-classes, build\libs | Out-Null

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
Invoke-JavaTest "com.openrange.PaxChartTimeCoordsTest"
Invoke-JavaTest "com.openrange.PaxTrendTriangleDedupTest"
Invoke-JavaTest "com.openrange.PaxTrendSignalRuntimeStatusTest"

$jarPath = "build\libs\openrange-release.jar"
& "$javaBin\jar.exe" @("--create", "--file", $jarPath, "-C", "build\classes", ".")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $jarPath -Destination "build\libs\openrange-release-fixed.jar" -Force
Write-Host "Built $jarPath"
