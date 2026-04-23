#pragma once

#include <QTimer>
#include <QVariantMap>

#include "AbstractJsonRpcClient.h"

class ShellSessionController : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QVariantMap viewModel READ viewModel NOTIFY viewModelChanged)
    Q_PROPERTY(bool leftRailCollapsed READ leftRailCollapsed NOTIFY leftRailCollapsedChanged)
    Q_PROPERTY(bool rightInspectorCollapsed READ rightInspectorCollapsed NOTIFY rightInspectorCollapsedChanged)
    Q_PROPERTY(bool bottomStripCollapsed READ bottomStripCollapsed NOTIFY bottomStripCollapsedChanged)
    Q_PROPERTY(QString sourceMode READ sourceMode WRITE setSourceMode NOTIFY sourceModeChanged)
    Q_PROPERTY(QString requestedPacketId READ requestedPacketId NOTIFY requestedPacketIdChanged)
    Q_PROPERTY(QString pendingAction READ pendingAction NOTIFY pendingActionChanged)
    Q_PROPERTY(QString actionErrorMessage READ actionErrorMessage NOTIFY actionErrorMessageChanged)
    Q_PROPERTY(bool noteDialogOpen READ noteDialogOpen NOTIFY noteDialogOpenChanged)
    Q_PROPERTY(QString noteDialogAction READ noteDialogAction NOTIFY noteDialogActionChanged)
    Q_PROPERTY(QString noteDialogTitle READ noteDialogTitle NOTIFY noteDialogTitleChanged)
    Q_PROPERTY(QString noteDraft READ noteDraft WRITE setNoteDraft NOTIFY noteDraftChanged)
    Q_PROPERTY(int terminalNoteMinChars READ terminalNoteMinChars NOTIFY terminalNoteMinCharsChanged)

public:
    explicit ShellSessionController(AbstractJsonRpcClient *client = nullptr, QObject *parent = nullptr);

    QVariantMap viewModel() const;
    bool leftRailCollapsed() const;
    bool rightInspectorCollapsed() const;
    bool bottomStripCollapsed() const;
    QString sourceMode() const;
    QString requestedPacketId() const;
    QString pendingAction() const;
    QString actionErrorMessage() const;
    bool noteDialogOpen() const;
    QString noteDialogAction() const;
    QString noteDialogTitle() const;
    QString noteDraft() const;
    int terminalNoteMinChars() const;

    void setSourceMode(const QString &sourceMode);
    void setNoteDraft(const QString &noteDraft);

    Q_INVOKABLE void startSession();
    Q_INVOKABLE void refresh();
    Q_INVOKABLE void toggleLeftRailCollapsed();
    Q_INVOKABLE void toggleRightInspectorCollapsed();
    Q_INVOKABLE void toggleBottomStripCollapsed();
    Q_INVOKABLE void selectPacket(const QString &packetId);
    Q_INVOKABLE void claimPacket();
    Q_INVOKABLE void approvePacket();
    Q_INVOKABLE void rejectPacket();
    Q_INVOKABLE void reworkPacket();
    Q_INVOKABLE void submitPendingReviewAction();
    Q_INVOKABLE void cancelPendingReviewAction();

    static bool validateShellViewDto(const QVariantMap &viewModel, QString *errorMessage = nullptr);
    static QVariantMap synthesizeViewState(
        const QString &sourceMode,
        const QString &viewState,
        const QString &message,
        bool retryable,
        const QString &requestedPacketId = QString());
    static QString clientViewRevision(const QVariantMap &viewModel);

signals:
    void viewModelChanged();
    void leftRailCollapsedChanged();
    void rightInspectorCollapsedChanged();
    void bottomStripCollapsedChanged();
    void sourceModeChanged();
    void requestedPacketIdChanged();
    void pendingActionChanged();
    void actionErrorMessageChanged();
    void noteDialogOpenChanged();
    void noteDialogActionChanged();
    void noteDialogTitleChanged();
    void noteDraftChanged();
    void terminalNoteMinCharsChanged();

private slots:
    void onClientReady();
    void onRpcResult(const QString &method, const QVariantMap &result);
    void onRpcError(const QString &method, const QVariantMap &error);
    void onInvalidReply(const QString &message);
    void onTransportError(const QString &message);

private:
    void requestInitialize();
    void requestCurrentView(bool refreshRequest);
    void sendMutationRequest(const QString &method, const QVariantMap &params, const QString &actionName);
    void applyViewModel(const QVariantMap &viewModel);
    void applySynthesizedState(
        const QString &viewState,
        const QString &message,
        bool retryable,
        const QString &requestedPacketId = QString());
    void openNoteDialog(const QString &action, const QString &title);
    void closeNoteDialog();
    void setRequestedPacketId(const QString &packetId);
    void setPendingAction(const QString &pendingAction);
    void setActionErrorMessage(const QString &actionErrorMessage);
    void setNoteDialogOpen(bool open);
    void setNoteDialogAction(const QString &action);
    void setNoteDialogTitle(const QString &title);
    QString currentPacketId() const;
    static bool isMutationMethod(const QString &method);
    QString mutationErrorMessage(const QVariantMap &error) const;
    QString resolveServiceProgram() const;
    QVariantMap initializeParams() const;
    void setLeftRailCollapsed(bool collapsed);
    void setRightInspectorCollapsed(bool collapsed);
    void setBottomStripCollapsed(bool collapsed);

    AbstractJsonRpcClient *m_client = nullptr;
    QTimer m_refreshTimer;
    QVariantMap m_viewModel;
    bool m_leftRailCollapsed = false;
    bool m_rightInspectorCollapsed = false;
    bool m_bottomStripCollapsed = false;
    bool m_initialized = false;
    bool m_ownsClient = false;
    QString m_sourceMode;
    QString m_requestedPacketId;
    QString m_pendingAction;
    QString m_actionErrorMessage;
    bool m_noteDialogOpen = false;
    QString m_noteDialogAction;
    QString m_noteDialogTitle;
    QString m_noteDraft;
    QString m_noteDialogPacketId;
    int m_terminalNoteMinChars = 0;
};
