#pragma once

#include <QHash>
#include <QProcess>

#include "AbstractJsonRpcClient.h"

class JsonRpcFramedClient : public AbstractJsonRpcClient
{
    Q_OBJECT

public:
    explicit JsonRpcFramedClient(QObject *parent = nullptr);

    void start(const QString &program, const QStringList &arguments) override;
    bool sendRequest(const QString &method, const QVariantMap &params) override;

private slots:
    void onReadyReadStandardOutput();
    void onReadyReadStandardError();
    void onProcessError(QProcess::ProcessError error);
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);

private:
    void processBufferedFrames();
    bool dispatchResponseFrame(const QByteArray &payload);
    void failInvalidReply(const QString &message);

    QProcess m_process;
    QByteArray m_stdoutBuffer;
    QHash<qint64, QString> m_pendingMethods;
    qint64 m_nextRequestId = 1;
};
