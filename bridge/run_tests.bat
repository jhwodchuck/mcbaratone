@echo off
echo Compiling tests...
if not exist build\standalone mkdir build\standalone

javac -cp libs\gson-2.10.1.jar -d build\standalone src\main\java\com\minecraftbot\baritone\BridgeContext.java src\main\java\com\minecraftbot\baritone\BridgeController.java src\test\java\com\minecraftbot\baritone\BridgeControllerTest.java

if %ERRORLEVEL% NEQ 0 (
    echo Compilation Failed!
    exit /b %ERRORLEVEL%
)

echo Running tests...
java -cp "build\standalone;libs\gson-2.10.1.jar" com.minecraftbot.baritone.BridgeControllerTest
