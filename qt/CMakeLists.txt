cmake_minimum_required(VERSION 3.21)

project(skynet_qt_shell VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Qml Quick Test)

qt_standard_project_setup(REQUIRES 6.5)

add_library(skynet_qt_shell_core STATIC
    src/AbstractJsonRpcClient.h
    src/JsonRpcFramedClient.h
    src/JsonRpcFramedClient.cpp
    src/ShellSessionController.h
    src/ShellSessionController.cpp
)

target_include_directories(skynet_qt_shell_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(skynet_qt_shell_core PUBLIC Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick)

qt_add_executable(skynet_qt_shell
    src/main.cpp
)

target_link_libraries(skynet_qt_shell PRIVATE skynet_qt_shell_core)

qt_add_qml_module(skynet_qt_shell
    URI SkynetShell
    VERSION 1.0
    QML_FILES
        qml/AppWindow.qml
        qml/StatusStrip.qml
        qml/TabRail.qml
        qml/LeftControlRail.qml
        qml/CenterPacketPane.qml
        qml/RightInspectorPane.qml
        qml/BottomBlotterPane.qml
        qml/ShellGeometryState.qml
)

enable_testing()

qt_add_executable(skynet_qt_shell_tests
    tests/ShellSessionControllerTests.cpp
)

target_link_libraries(skynet_qt_shell_tests PRIVATE skynet_qt_shell_core Qt6::Test Qt6::Core)

add_test(NAME skynet_qt_shell_tests COMMAND skynet_qt_shell_tests)
