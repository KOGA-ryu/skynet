#pragma once

#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>

class AbstractJsonRpcClient : public QObject
{
    Q_OBJECT

public:
    explicit AbstractJsonRpcClient(QObject *parent = nullptr)
        : QObject(parent)
    {
    }

    ~AbstractJsonRpcClient() override = default;

    virtual void start(const QString &program, const QStringList &arguments) = 0;
    virtual bool sendRequest(const QString &method, const QVariantMap &params) = 0;

signals:
    void ready();
    void rpcResult(const QString &method, const QVariantMap &result);
    void rpcError(const QString &method, const QVariantMap &error);
    void invalidReply(const QString &message);
    void transportError(const QString &message);
};
