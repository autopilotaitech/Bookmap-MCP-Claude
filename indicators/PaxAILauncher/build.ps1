$ErrorActionPreference = "Stop"

$bookmapLib = "C:\Program Files\Bookmap\lib"
$javaBin = "C:\Program Files\Bookmap\jre\bin"
$classpath = "$bookmapLib\bm-l1api.jar;$bookmapLib\bm-simplified-api-wrapper.jar;$bookmapLib\util.jar"

# Java 17 needs --add-opens to allow the reflective env-var hack in the test.
$jvmTestOpens = @(
    "--add-opens", "java.base/java.lang=ALL-UNNAMED"
)

function Invoke-JavaTest($className) {
    & "$javaBin\java.exe" ($jvmTestOpens + @("-cp", "build\classes;build\test-classes;$classpath", $className))
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Remove-Item -Recurse -Force build\classes, build\test-classes, build\libs -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path build\classes, build\test-classes, build\libs | Out-Null

$mainSources = Get-ChildItem -Path src\main\java -Recurse -Filter *.java |
    ForEach-Object { $_.FullName }
& "$javaBin\javac.exe" @("-cp", $classpath, "-d", "build\classes") $mainSources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$testSources = Get-ChildItem -Path src\test\java -Recurse -Filter *.java | ForEach-Object { $_.FullName }
& "$javaBin\javac.exe" @("-cp", "build\classes;$classpath", "-d", "build\test-classes") $testSources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Invoke-JavaTest "com.paxai.PaxAILauncherLifecycleTest"

$jarPath = "build\libs\paxai-launcher-release.jar"
& "$javaBin\jar.exe" @("--create", "--file", $jarPath, "-C", "build\classes", ".")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built $jarPath"
