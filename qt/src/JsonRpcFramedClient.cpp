#include "JsonRpcFramedClient.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QLoggingCategory>

namespace {
QString responseMessageForError(QProcess::ProcessError error)
{
    switch (error) {
    case QProcess::FailedToStart:
        return QStringLiteral("Shell service process failed to start.");
    case QProcess::Crashed:
        return QStringLiteral("Shell service process crashed.");
    case QProcess::Timedout:
        return QStringLiteral("Shell service process timed out.");
    case QProcess::WriteError:
        return QStringLiteral("Shell service write failed.");
    case QProcess::ReadError:
        return QStringLiteral("Shell service read failed.");
    case QProcess::UnknownError:
    default:
        return QStringLiteral("Shell service process reported an unknown error.");
    }
}
}

JsonRpcFramedClient::JsonRpcFramedClient(QObject *parent)
    : AbstractJsonRpcClient(parent)
{
    connect(&m_process, &QProcess::started, this, &JsonRpcFramedClient::ready);
    connect(&m_process, &QProcess::readyReadStandardOutput, this, &JsonRpcFramedClient::onReadyReadStandardOutput);
    connect(&m_process, &QProcess::readyReadStandardError, this, &JsonRpcFramedClient::onReadyReadStandardError);
    connect(&m_process, &QProcess::errorOccurred, this, &JsonRpcFramedClient::onProcessError);
    connect(&m_process, &QProcess::finished, this, &JsonRpcFramedClient::onProcessFinished);
}

void JsonRpcFramedClient::start(const QString &program, const QStringList &arguments)
{
    if (m_process.state() != QProcess::NotRunning) {
        emit transportError(QStringLiteral("Shell service process is already running."));
        return;
    }
    m_stdoutBuffer.clear();
    m_pendingMethods.clear();
    m_process.start(program, arguments);
}

bool JsonRpcFramedClient::sendRequest(const QString &method, const QVariantMap &params)
{
    if (m_process.state() != QProcess::Running) {
        emit transportError(QStringLiteral("Shell service process is not running."));
        return false;
    }
    if (!m_pendingMethods.isEmpty()) {
        emit transportError(QStringLiteral("Shell service only allows one request in flight."));
        return false;
    }

    const qint64 requestId = m_nextRequestId++;
    const QJsonObject payload {
        {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
        {QStringLiteral("id"), requestId},
        {QStringLiteral("method"), method},
        {QStringLiteral("params"), QJsonObject::fromVariantMap(params)},
    };
    const QByteArray body = QJsonDocument(payload).toJson(QJsonDocument::Compact);
    QByteArray frame = "Content-Length: " + QByteArray::number(body.size()) + "\r\n\r\n";
    frame.append(body);
    const qint64 written = m_process.write(frame);
    if (written != frame.size()) {
        emit transportError(QStringLiteral("Failed to write a complete request to the shell service."));
        return false;
    }
    m_pendingMethods.insert(requestId, method);
    return true;
}

void JsonRpcFramedClient::onReadyReadStandardOutput()
{
    m_stdoutBuffer.append(m_process.readAllStandardOutput());
    processBufferedFrames();
}

void JsonRpcFramedClient::onReadyReadStandardError()
{
    const QByteArray logBytes = m_process.readAllStandardError();
    if (!logBytes.isEmpty()) {
        qWarning().noquote() << QString::fromUtf8(logBytes).trimmed();
    }
}

void JsonRpcFramedClient::onProcessError(QProcess::ProcessError error)
{
    emit transportError(responseMessageForError(error));
}

void JsonRpcFramedClient::onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    if (exitStatus == QProcess::CrashExit) {
        emit transportError(QStringLiteral("Shell service process crashed."));
        return;
    }
    if (exitCode != 0 && exitCode != 2) {
        emit transportError(QStringLiteral("Shell service process exited unexpectedly."));
    }
}

void JsonRpcFramedClient::processBufferedFrames()
{
    while (true) {
        const QByteArray separator = QByteArrayLiteral("\r\n\r\n");
        const int headerEnd = m_stdoutBuffer.indexOf(separator);
        if (headerEnd < 0) {
            return;
        }
        const QByteArray headerBlock = m_stdoutBuffer.left(headerEnd);
        const QList<QByteArray> headerLines = headerBlock.split('\n');
        if (headerLines.size() != 1) {
            failInvalidReply(QStringLiteral("Shell service returned unsupported headers."));
            return;
        }
        QByteArray headerLine = headerLines.first().trimmed();
        if (!headerLine.startsWith("Content-Length: ")) {
            failInvalidReply(QStringLiteral("Shell service reply is missing Content-Length."));
            return;
        }
        bool ok = false;
        const int contentLength = headerLine.mid(QByteArray("Content-Length: ").size()).toInt(&ok);
        if (!ok || contentLength < 0) {
            failInvalidReply(QStringLiteral("Shell service reply has an invalid Content-Length."));
            return;
        }
        const int frameSize = headerEnd + separator.size() + contentLength;
        if (m_stdoutBuffer.size() < frameSize) {
            return;
        }
        const QByteArray payload = m_stdoutBuffer.mid(headerEnd + separator.size(), contentLength);
        m_stdoutBuffer.remove(0, frameSize);
        if (!dispatchResponseFrame(payload)) {
            return;
        }
    }
}

bool JsonRpcFramedClient::dispatchResponseFrame(const QByteArray &payload)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(payload, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        failInvalidReply(QStringLiteral("Shell service returned malformed JSON-RPC."));
        return false;
    }

    const QJsonObject object = document.object();
    if (object.value(QStringLiteral("jsonrpc")).toString() != QStringLiteral("2.0")) {
        failInvalidReply(QStringLiteral("Shell service reply does not declare JSON-RPC 2.0."));
        return false;
    }
    if (!object.contains(QStringLiteral("id")) || !object.value(QStringLiteral("id")).isDouble()) {
        failInvalidReply(QStringLiteral("Shell service reply is missing a numeric request id."));
        return false;
    }
    const qint64 requestId = object.value(QStringLiteral("id")).toInteger();
    const QString method = m_pendingMethods.take(requestId);
    if (method.isEmpty()) {
        failInvalidReply(QStringLiteral("Shell service reply does not match an active request."));
        return false;
    }

    if (object.contains(QStringLiteral("error"))) {
        if (!object.value(QStringLiteral("error")).isObject()) {
            failInvalidReply(QStringLiteral("Shell service error payload is not an object."));
            return false;
        }
        emit rpcError(method, object.value(QStringLiteral("error")).toObject().toVariantMap());
        return true;
    }
    if (!object.contains(QStringLiteral("result")) || !object.value(QStringLiteral("result")).isObject()) {
        failInvalidReply(QStringLiteral("Shell service result payload is not an object."));
        return false;
    }
    emit rpcResult(method, object.value(QStringLiteral("result")).toObject().toVariantMap());
    return true;
}

void JsonRpcFramedClient::failInvalidReply(const QString &message)
{
    m_pendingMethods.clear();
    m_stdoutBuffer.clear();
    emit invalidReply(message);
}
