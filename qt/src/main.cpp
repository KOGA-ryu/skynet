#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>

#include "ShellSessionController.h"

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("skynet_qt_shell"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.1.0"));

    QQmlApplicationEngine engine;
    ShellSessionController controller;
    engine.rootContext()->setContextProperty(QStringLiteral("shellSession"), &controller);
    engine.loadFromModule(QStringLiteral("SkynetShell"), QStringLiteral("AppWindow"));
    if (engine.rootObjects().isEmpty()) {
        return -1;
    }
    controller.startSession();
    return app.exec();
}
