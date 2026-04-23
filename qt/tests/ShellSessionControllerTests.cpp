#include <QtTest>

#include "ShellSessionController.h"

class FakeJsonRpcClient final : public AbstractJsonRpcClient
{
    Q_OBJECT

public:
    explicit FakeJsonRpcClient(QObject *parent = nullptr)
        : AbstractJsonRpcClient(parent)
    {
    }

    void start(const QString &program, const QStringList &arguments) override
    {
        lastProgram = program;
        lastArguments = arguments;
    }

    bool sendRequest(const QString &method, const QVariantMap &params) override
    {
        sentMethods.append(method);
        sentParams.append(params);
        return true;
    }

    QString lastProgram;
    QStringList lastArguments;
    QStringList sentMethods;
    QList<QVariantMap> sentParams;
};

class ShellSessionControllerTests : public QObject
{
    Q_OBJECT

private slots:
    void handshakeSuccessRequestsFixtureView();
    void selectPacketRequestsStorageViewImmediately();
    void approveDialogAllowsEmptyNoteAndSendsNull();
    void rejectDialogEnforcesMinimumNoteLength();
    void mutationErrorPreservesCurrentViewModel();
    void transportFailureSynthesizesServiceUnavailable();
    void schemaInvalidReplySynthesizesInvalidReply();
    void unchangedRevisionSkipsReplacement();
};

namespace {
QVariantMap initializeResult()
{
    return QVariantMap {
        {QStringLiteral("protocol_version"), QStringLiteral("1.0")},
        {QStringLiteral("dto_version"), QStringLiteral("1.0")},
        {QStringLiteral("service_version"), QStringLiteral("0.2.0")},
        {QStringLiteral("capabilities"), QVariantList {QStringLiteral("fixture_view")}},
        {QStringLiteral("interaction_mode"), QStringLiteral("display_only")},
        {QStringLiteral("transport"), QStringLiteral("jsonrpc_stdio_content_length")},
        {QStringLiteral("reviewer_identity"), QVariantMap {
             {QStringLiteral("status"), QStringLiteral("present")},
             {QStringLiteral("reviewer_name"), QStringLiteral("ace")},
             {QStringLiteral("source"), QStringLiteral("client_env")},
         }},
        {QStringLiteral("review_note_policy"), QVariantMap {
             {QStringLiteral("approve_note_required"), false},
             {QStringLiteral("terminal_note_min_chars"), 24},
         }},
    };
}

QVariantMap shellView(const QString &packetId = QStringLiteral("pkt_001"))
{
    QVariantMap view = ShellSessionController::synthesizeViewState(
        QStringLiteral("storage"),
        QStringLiteral("ready"),
        QString(),
        false);
    view.insert(QStringLiteral("active_packet_id"), packetId);
    view.insert(QStringLiteral("selection_reason"), QStringLiteral("requested_packet"));
    QVariantMap status = view.value(QStringLiteral("status")).toMap();
    status.insert(QStringLiteral("title"), QStringLiteral("Active packet %1").arg(packetId));
    status.insert(QStringLiteral("queue_status"), QStringLiteral("pending"));
    status.insert(QStringLiteral("assigned_reviewer"), QVariant());
    status.insert(QStringLiteral("reviewer_identity"), QVariantMap {
         {QStringLiteral("status"), QStringLiteral("present")},
         {QStringLiteral("reviewer_name"), QStringLiteral("ace")},
         {QStringLiteral("source"), QStringLiteral("client_env")},
     });
    view.insert(QStringLiteral("status"), status);
    QVariantMap inspector = view.value(QStringLiteral("right_inspector")).toMap();
    inspector.insert(QStringLiteral("review_actions"), QVariantMap {
         {QStringLiteral("claim_visible"), true},
         {QStringLiteral("claim_enabled"), true},
         {QStringLiteral("approve_visible"), true},
         {QStringLiteral("reject_visible"), true},
         {QStringLiteral("rework_visible"), true},
         {QStringLiteral("approve_enabled"), false},
         {QStringLiteral("reject_enabled"), false},
         {QStringLiteral("rework_enabled"), false},
         {QStringLiteral("disabled_reason"), QStringLiteral("Claim this packet before approving, rejecting, or requesting rework.")},
     });
    view.insert(QStringLiteral("right_inspector"), inspector);
    view.insert(QStringLiteral("view_revision"), ShellSessionController::clientViewRevision(view));
    return view;
}
}

void ShellSessionControllerTests::handshakeSuccessRequestsFixtureView()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);
    controller.setSourceMode(QStringLiteral("fixture"));

    controller.startSession();
    QCOMPARE(client.lastArguments, QStringList() << QStringLiteral("--stdio"));

    emit client.ready();
    QCOMPARE(client.sentMethods.value(0), QStringLiteral("shell.initialize"));
    QCOMPARE(client.sentParams.value(0).value(QStringLiteral("client_name")).toString(),
        QStringLiteral("skynet_qt_shell"));

    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());

    QCOMPARE(client.sentMethods.value(1), QStringLiteral("shell.get_fixture_view"));
}

void ShellSessionControllerTests::selectPacketRequestsStorageViewImmediately()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    controller.startSession();
    emit client.ready();
    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());
    emit client.rpcResult(QStringLiteral("shell.get_storage_view"), shellView(QStringLiteral("pkt_001")));

    controller.selectPacket(QStringLiteral("pkt_002"));

    QCOMPARE(client.sentMethods.last(), QStringLiteral("shell.get_storage_view"));
    QCOMPARE(client.sentParams.last().value(QStringLiteral("packet_id")).toString(), QStringLiteral("pkt_002"));
    QCOMPARE(controller.requestedPacketId(), QStringLiteral("pkt_002"));
}

void ShellSessionControllerTests::approveDialogAllowsEmptyNoteAndSendsNull()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    controller.startSession();
    emit client.ready();
    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());
    emit client.rpcResult(QStringLiteral("shell.get_storage_view"), shellView(QStringLiteral("pkt_approve")));

    controller.approvePacket();
    QVERIFY(controller.noteDialogOpen());
    QCOMPARE(controller.noteDialogAction(), QStringLiteral("approve"));

    controller.submitPendingReviewAction();

    QCOMPARE(client.sentMethods.last(), QStringLiteral("shell.approve_packet"));
    QVERIFY(!client.sentParams.last().contains(QStringLiteral("notes"))
        || !client.sentParams.last().value(QStringLiteral("notes")).isValid());
}

void ShellSessionControllerTests::rejectDialogEnforcesMinimumNoteLength()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    controller.startSession();
    emit client.ready();
    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());
    emit client.rpcResult(QStringLiteral("shell.get_storage_view"), shellView(QStringLiteral("pkt_reject")));

    controller.rejectPacket();
    controller.setNoteDraft(QStringLiteral("too short"));
    controller.submitPendingReviewAction();

    QCOMPARE(controller.actionErrorMessage(), QStringLiteral("Review note must be at least 24 characters."));
    QVERIFY(client.sentMethods.last() != QStringLiteral("shell.reject_packet"));
}

void ShellSessionControllerTests::mutationErrorPreservesCurrentViewModel()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    controller.startSession();
    emit client.ready();
    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());
    const QVariantMap currentView = shellView(QStringLiteral("pkt_hold"));
    emit client.rpcResult(QStringLiteral("shell.get_storage_view"), currentView);

    controller.claimPacket();
    emit client.rpcError(QStringLiteral("shell.claim_storage_packet"), QVariantMap {
            {QStringLiteral("message"), QStringLiteral("Packet is claimed by another reviewer.")},
            {QStringLiteral("data"), QVariantMap {
                 {QStringLiteral("details"), QVariantMap {
                      {QStringLiteral("reason_kind"), QStringLiteral("claimed_by_other_reviewer")},
                      {QStringLiteral("assigned_reviewer"), QStringLiteral("bea")},
                  }},
             }},
        });

    QCOMPARE(controller.actionErrorMessage(),
        QStringLiteral("Claimed by bea. Only the assigned reviewer can complete review."));
    QCOMPARE(controller.viewModel().value(QStringLiteral("view_revision")).toString(),
        currentView.value(QStringLiteral("view_revision")).toString());
}

void ShellSessionControllerTests::transportFailureSynthesizesServiceUnavailable()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    QSignalSpy viewSpy(&controller, &ShellSessionController::viewModelChanged);
    controller.startSession();
    emit client.transportError(QStringLiteral("failed to start"));

    QCOMPARE(viewSpy.count(), 1);
    QCOMPARE(controller.viewModel().value(QStringLiteral("view_state")).toString(),
        QStringLiteral("service_unavailable"));
}

void ShellSessionControllerTests::schemaInvalidReplySynthesizesInvalidReply()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);
    controller.setSourceMode(QStringLiteral("fixture"));

    controller.startSession();
    emit client.ready();
    emit client.rpcResult(QStringLiteral("shell.initialize"), initializeResult());

    QSignalSpy viewSpy(&controller, &ShellSessionController::viewModelChanged);
    emit client.rpcResult(QStringLiteral("shell.get_fixture_view"), QVariantMap {
            {QStringLiteral("not_shell"), true},
        });

    QVERIFY(viewSpy.count() >= 1);
    QCOMPARE(controller.viewModel().value(QStringLiteral("view_state")).toString(),
        QStringLiteral("invalid_reply"));
}

void ShellSessionControllerTests::unchangedRevisionSkipsReplacement()
{
    FakeJsonRpcClient client;
    ShellSessionController controller(&client);

    QVariantMap synthesized = ShellSessionController::synthesizeViewState(
        QStringLiteral("storage"),
        QStringLiteral("invalid_reply"),
        QStringLiteral("same"),
        true);

    QSignalSpy viewSpy(&controller, &ShellSessionController::viewModelChanged);
    emit client.invalidReply(QStringLiteral("same"));
    const int firstCount = viewSpy.count();
    emit client.invalidReply(QStringLiteral("same"));
    QCOMPARE(viewSpy.count(), firstCount);
    QCOMPARE(controller.viewModel().value(QStringLiteral("view_revision")).toString(),
        synthesized.value(QStringLiteral("view_revision")).toString());
}

QTEST_MAIN(ShellSessionControllerTests)

#include "ShellSessionControllerTests.moc"
