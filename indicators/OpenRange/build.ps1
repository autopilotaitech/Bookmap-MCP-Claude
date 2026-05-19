$ErrorActionPreference = "Stop"

$bookmapLib = "C:\Program Files\Bookmap\lib"
$javaBin = "C:\Program Files\Bookmap\jre\bin"
$classpath = "$bookmapLib\bm-l1api.jar;$bookmapLib\bm-simplified-api-wrapper.jar;$bookmapLib\util.jar"

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
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeCalculatorTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeSignalEngineTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeOrderFlowTrackerTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeSignalFormatterTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeSignalCsvLoggerTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeRollingStatsTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeSignalColorTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeCrossMarketTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeLogPathTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeRollingPercentileTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeQualityTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeFeatureCacheTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeSignalQualityGateTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeDiagnosticsTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeStateCacheTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxOpeningRangeModuleConcurrencyTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxHeatwaveSnapshotParserTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxHeatwavePainterTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$javaBin\java.exe" @("-cp", "build\classes;build\test-classes;$classpath", "com.openrange.PaxHeatwaveFetcherTest")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$jarPath = "build\libs\openrange-release.jar"
& "$javaBin\jar.exe" @("--create", "--file", $jarPath, "-C", "build\classes", ".")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $jarPath -Destination "build\libs\openrange-release-fixed.jar" -Force
Write-Host "Built $jarPath"
