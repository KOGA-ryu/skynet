#include "ShellSessionController.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include "JsonRpcFramedClient.h"

namespace {
QString stringValue(const QVariantMap &map, const QString &key)
{
    return map.value(key).toString();
}

bool containsKeys(const QVariantMap &map, const QStringList &keys, QString *errorMessage)
{
    for (const QString &key : keys) {
        if (!map.contains(key)) {
            if (errorMessage != nullptr) {
                *errorMessage = QStringLiteral("Missing required key: %1").arg(key);
            }
            return false;
        }
    }
    return true;
}

QVariant normalizeVariant(const QVariant &value)
{
    if (value.typeId() == QMetaType::QVariantMap) {
        QVariantMap normalized;
        const QVariantMap original = value.toMap();
        for (auto it = original.cbegin(); it != original.cend(); ++it) {
            normalized.insert(it.key(), normalizeVariant(it.value()));
        }
        return normalized;
    }
    if (value.typeId() == QMetaType::QVariantList) {
        QVariantList normalized;
        const QVariantList original = value.toList();
        normalized.reserve(original.size());
        for (const QVariant &item : original) {
            normalized.append(normalizeVariant(item));
        }
        return normalized;
    }
    return value;
}

QVariantMap defaultFrame()
{
    return QVariantMap {
        {QStringLiteral("app_min_width_px"), 1440},
        {QStringLiteral("app_min_height_px"), 900},
        {QStringLiteral("status_strip_height_px"), 40},
        {QStringLiteral("tab_rail_height_px"), 32},
        {QStringLiteral("left_rail_default_width_px"), 272},
        {QStringLiteral("left_rail_collapsed_width_px"), 40},
        {QStringLiteral("left_rail_min_width_px"), 232},
        {QStringLiteral("right_inspector_default_width_px"), 360},
        {QStringLiteral("right_inspector_collapsed_width_px"), 40},
        {QStringLiteral("right_inspector_min_width_px"), 320},
        {QStringLiteral("bottom_strip_default_height_px"), 192},
        {QStringLiteral("bottom_strip_collapsed_height_px"), 28},
        {QStringLiteral("bottom_strip_min_height_px"), 144},
        {QStringLiteral("center_min_width_px"), 720},
        {QStringLiteral("center_min_height_px"), 420},
        {QStringLiteral("refresh_interval_ms"), 2000},
    };
}

QVariantMap emptyPacketSummary()
{
    return QVariantMap {
        {QStringLiteral("packet_id"), QString()},
        {QStringLiteral("document_id"), QString()},
        {QStringLiteral("version"), QString()},
        {QStringLiteral("subject_label"), QString()},
        {QStringLiteral("title"), QString()},
        {QStringLiteral("render_status"), QStringLiteral("unavailable")},
    };
}

QVariantMap emptyValidationSummary()
{
    return QVariantMap {
        {QStringLiteral("run_id"), QString()},
        {QStringLiteral("status"), QStringLiteral("unavailable")},
        {QStringLiteral("blocker_count"), 0},
        {QStringLiteral("issue_count"), 0},
        {QStringLiteral("reviewed"), false},
    };
}

QVariantMap emptyDiffSummary()
{
    return QVariantMap {
        {QStringLiteral("diff_target_id"), QString()},
        {QStringLiteral("change_count"), 0},
        {QStringLiteral("reviewed"), false},
        {QStringLiteral("summary"), QString()},
    };
}

QVariantMap disabledReviewActions()
{
    return QVariantMap {
        {QStringLiteral("claim_visible"), false},
        {QStringLiteral("claim_enabled"), false},
        {QStringLiteral("approve_visible"), true},
        {QStringLiteral("reject_visible"), true},
        {QStringLiteral("rework_visible"), true},
        {QStringLiteral("approve_enabled"), false},
        {QStringLiteral("reject_enabled"), false},
        {QStringLiteral("rework_enabled"), false},
        {QStringLiteral("disabled_reason"), QString()},
    };
}

QVariantMap missingReviewerIdentity()
{
    return QVariantMap {
        {QStringLiteral("status"), QStringLiteral("missing")},
        {QStringLiteral("reviewer_name"), QVariant()},
        {QStringLiteral("source"), QStringLiteral("missing")},
    };
}

QVariantMap unavailableGate(const QString &label)
{
    return QVariantMap {
        {QStringLiteral("source"), QStringLiteral("unavailable")},
        {QStringLiteral("label"), label},
        {QStringLiteral("required_fields_loaded"), false},
        {QStringLiteral("validation_status"), QStringLiteral("unavailable")},
        {QStringLiteral("blocker_count"), 0},
        {QStringLiteral("diff_reviewed"), false},
        {QStringLiteral("evidence_reviewed"), false},
        {QStringLiteral("stale"), false},
        {QStringLiteral("dirty"), false},
        {QStringLiteral("approve_enabled"), false},
        {QStringLiteral("reject_enabled"), false},
        {QStringLiteral("rework_enabled"), false},
        {QStringLiteral("active_diff_target_id"), QVariant()},
        {QStringLiteral("active_evidence_id"), QVariant()},
        {QStringLiteral("active_validation_issue_id"), QVariant()},
        {QStringLiteral("updated_at"), QString()},
    };
}
}

ShellSessionController::ShellSessionController(AbstractJsonRpcClient *client, QObject *parent)
    : QObject(parent)
    , m_client(client)
    , m_sourceMode(qEnvironmentVariableIsSet("SKYNET_SHELL_SOURCE_MODE")
                       ? QString::fromUtf8(qgetenv("SKYNET_SHELL_SOURCE_MODE"))
                       : QStringLiteral("storage"))
{
    if (m_client == nullptr) {
        m_client = new JsonRpcFramedClient(this);
        m_ownsClient = true;
    }

    connect(m_client, &AbstractJsonRpcClient::ready, this, &ShellSessionController::onClientReady);
    connect(m_client, &AbstractJsonRpcClient::rpcResult, this, &ShellSessionController::onRpcResult);
    connect(m_client, &AbstractJsonRpcClient::rpcError, this, &ShellSessionController::onRpcError);
    connect(m_client, &AbstractJsonRpcClient::invalidReply, this, &ShellSessionController::onInvalidReply);
    connect(m_client, &AbstractJsonRpcClient::transportError, this, &ShellSessionController::onTransportError);

    m_refreshTimer.setSingleShot(false);
    m_refreshTimer.setInterval(2000);
    connect(&m_refreshTimer, &QTimer::timeout, this, &ShellSessionController::refresh);

    m_viewModel = synthesizeViewState(m_sourceMode, QStringLiteral("service_unavailable"),
        QStringLiteral("Shell session has not started."), true);
}

QVariantMap ShellSessionController::viewModel() const
{
    return m_viewModel;
}

bool ShellSessionController::leftRailCollapsed() const
{
    return m_leftRailCollapsed;
}

bool ShellSessionController::rightInspectorCollapsed() const
{
    return m_rightInspectorCollapsed;
}

bool ShellSessionController::bottomStripCollapsed() const
{
    return m_bottomStripCollapsed;
}

QString ShellSessionController::sourceMode() const
{
    return m_sourceMode;
}

QString ShellSessionController::requestedPacketId() const
{
    return m_requestedPacketId;
}

QString ShellSessionController::pendingAction() const
{
    return m_pendingAction;
}

QString ShellSessionController::actionErrorMessage() const
{
    return m_actionErrorMessage;
}

bool ShellSessionController::noteDialogOpen() const
{
    return m_noteDialogOpen;
}

QString ShellSessionController::noteDialogAction() const
{
    return m_noteDialogAction;
}

QString ShellSessionController::noteDialogTitle() const
{
    return m_noteDialogTitle;
}

QString ShellSessionController::noteDraft() const
{
    return m_noteDraft;
}

int ShellSessionController::terminalNoteMinChars() const
{
    return m_terminalNoteMinChars;
}

void ShellSessionController::setSourceMode(const QString &sourceMode)
{
    const QString normalized = sourceMode == QStringLiteral("fixture")
        ? QStringLiteral("fixture")
        : QStringLiteral("storage");
    if (m_sourceMode == normalized) {
        return;
    }
    m_sourceMode = normalized;
    emit sourceModeChanged();
}

void ShellSessionController::setNoteDraft(const QString &noteDraft)
{
    const QString normalized = noteDraft;
    if (m_noteDraft == normalized) {
        return;
    }
    m_noteDraft = normalized;
    emit noteDraftChanged();
}

void ShellSessionController::startSession()
{
    m_initialized = false;
    m_refreshTimer.stop();
    setPendingAction(QString());
    setActionErrorMessage(QString());
    closeNoteDialog();
    setRequestedPacketId(QString());
    m_client->start(resolveServiceProgram(), {QStringLiteral("--stdio")});
}

void ShellSessionController::refresh()
{
    if (!m_initialized || m_sourceMode != QStringLiteral("storage")) {
        return;
    }
    requestCurrentView(true);
}

void ShellSessionController::toggleLeftRailCollapsed()
{
    setLeftRailCollapsed(!m_leftRailCollapsed);
}

void ShellSessionController::toggleRightInspectorCollapsed()
{
    setRightInspectorCollapsed(!m_rightInspectorCollapsed);
}

void ShellSessionController::toggleBottomStripCollapsed()
{
    setBottomStripCollapsed(!m_bottomStripCollapsed);
}

void ShellSessionController::selectPacket(const QString &packetId)
{
    setRequestedPacketId(packetId);
    setActionErrorMessage(QString());
    requestCurrentView(false);
}

void ShellSessionController::claimPacket()
{
    const QString packetId = currentPacketId();
    if (packetId.isEmpty()) {
        return;
    }
    setActionErrorMessage(QString());
    sendMutationRequest(QStringLiteral("shell.claim_storage_packet"),
        QVariantMap {{QStringLiteral("packet_id"), packetId}},
        QStringLiteral("claim"));
}

void ShellSessionController::approvePacket()
{
    openNoteDialog(QStringLiteral("approve"), QStringLiteral("Approve Packet"));
}

void ShellSessionController::rejectPacket()
{
    openNoteDialog(QStringLiteral("reject"), QStringLiteral("Reject Packet"));
}

void ShellSessionController::reworkPacket()
{
    openNoteDialog(QStringLiteral("rework"), QStringLiteral("Request Rework"));
}

void ShellSessionController::submitPendingReviewAction()
{
    if (m_noteDialogPacketId.isEmpty() || m_noteDialogAction.isEmpty()) {
        return;
    }
    const QString trimmedNote = m_noteDraft.trimmed();
    QVariantMap params {
        {QStringLiteral("packet_id"), m_noteDialogPacketId},
    };
    QString method;
    if (m_noteDialogAction == QStringLiteral("approve")) {
        method = QStringLiteral("shell.approve_packet");
        params.insert(QStringLiteral("notes"), trimmedNote.isEmpty() ? QVariant() : trimmedNote);
    } else {
        if (trimmedNote.size() < m_terminalNoteMinChars) {
            setActionErrorMessage(QStringLiteral("Review note must be at least %1 characters.").arg(m_terminalNoteMinChars));
            return;
        }
        method = m_noteDialogAction == QStringLiteral("reject")
            ? QStringLiteral("shell.reject_packet")
            : QStringLiteral("shell.rework_packet");
        params.insert(QStringLiteral("notes"), trimmedNote);
    }
    closeNoteDialog();
    setActionErrorMessage(QString());
    sendMutationRequest(method, params, m_noteDialogAction);
}

void ShellSessionController::cancelPendingReviewAction()
{
    closeNoteDialog();
}

bool ShellSessionController::validateShellViewDto(const QVariantMap &viewModel, QString *errorMessage)
{
    if (!containsKeys(viewModel,
            {QStringLiteral("protocol_version"), QStringLiteral("dto_version"),
                QStringLiteral("service_version"), QStringLiteral("source_mode"),
                QStringLiteral("view_state"), QStringLiteral("view_revision"),
                QStringLiteral("interaction_mode"), QStringLiteral("active_packet_id"),
                QStringLiteral("requested_packet_id"), QStringLiteral("selection_reason"),
                QStringLiteral("frame"), QStringLiteral("status"), QStringLiteral("tabs"),
                QStringLiteral("gate"), QStringLiteral("left_rail"),
                QStringLiteral("center_surface"), QStringLiteral("right_inspector"),
                QStringLiteral("bottom_strip"), QStringLiteral("error")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("frame")).toMap(),
            {QStringLiteral("app_min_width_px"), QStringLiteral("app_min_height_px"),
                QStringLiteral("status_strip_height_px"), QStringLiteral("tab_rail_height_px"),
                QStringLiteral("left_rail_default_width_px"),
                QStringLiteral("left_rail_collapsed_width_px"),
                QStringLiteral("left_rail_min_width_px"),
                QStringLiteral("right_inspector_default_width_px"),
                QStringLiteral("right_inspector_collapsed_width_px"),
                QStringLiteral("right_inspector_min_width_px"),
                QStringLiteral("bottom_strip_default_height_px"),
                QStringLiteral("bottom_strip_collapsed_height_px"),
                QStringLiteral("bottom_strip_min_height_px"),
                QStringLiteral("center_min_width_px"), QStringLiteral("center_min_height_px"),
                QStringLiteral("refresh_interval_ms")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("gate")).toMap(),
            {QStringLiteral("source"), QStringLiteral("label"),
                QStringLiteral("required_fields_loaded"),
                QStringLiteral("validation_status"), QStringLiteral("blocker_count"),
                QStringLiteral("diff_reviewed"), QStringLiteral("evidence_reviewed"),
                QStringLiteral("stale"), QStringLiteral("dirty"),
                QStringLiteral("approve_enabled"), QStringLiteral("reject_enabled"),
                QStringLiteral("rework_enabled"), QStringLiteral("active_diff_target_id"),
                QStringLiteral("active_evidence_id"),
                QStringLiteral("active_validation_issue_id"), QStringLiteral("updated_at")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("status")).toMap(),
            {QStringLiteral("title"), QStringLiteral("detail"), QStringLiteral("source_label"),
                QStringLiteral("last_updated_at"), QStringLiteral("queue_status"),
                QStringLiteral("assigned_reviewer"), QStringLiteral("reviewer_identity"),
                QStringLiteral("last_action_receipt")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("left_rail")).toMap(),
            {QStringLiteral("pane_id"), QStringLiteral("title"),
                QStringLiteral("component_state"), QStringLiteral("collapsed"),
                QStringLiteral("visible"), QStringLiteral("banner_kind"),
                QStringLiteral("banner_text"), QStringLiteral("rows")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("center_surface")).toMap(),
            {QStringLiteral("pane_id"), QStringLiteral("title"),
                QStringLiteral("component_state"), QStringLiteral("collapsed"),
                QStringLiteral("visible"), QStringLiteral("banner_kind"),
                QStringLiteral("banner_text"), QStringLiteral("packet_summary"),
                QStringLiteral("validation_summary"), QStringLiteral("diff_summary")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("right_inspector")).toMap(),
            {QStringLiteral("pane_id"), QStringLiteral("title"),
                QStringLiteral("component_state"), QStringLiteral("collapsed"),
                QStringLiteral("visible"), QStringLiteral("banner_kind"),
                QStringLiteral("banner_text"), QStringLiteral("evidence_rows"),
                QStringLiteral("review_actions")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("right_inspector"))
                .toMap()
                .value(QStringLiteral("review_actions"))
                .toMap(),
            {QStringLiteral("claim_visible"), QStringLiteral("claim_enabled"),
                QStringLiteral("approve_visible"), QStringLiteral("reject_visible"),
                QStringLiteral("rework_visible"), QStringLiteral("approve_enabled"),
                QStringLiteral("reject_enabled"), QStringLiteral("rework_enabled"),
                QStringLiteral("disabled_reason")},
            errorMessage)) {
        return false;
    }
    if (!containsKeys(viewModel.value(QStringLiteral("bottom_strip")).toMap(),
            {QStringLiteral("pane_id"), QStringLiteral("title"),
                QStringLiteral("component_state"), QStringLiteral("collapsed"),
                QStringLiteral("visible"), QStringLiteral("banner_kind"),
                QStringLiteral("banner_text"), QStringLiteral("event_rows")},
            errorMessage)) {
        return false;
    }
    return true;
}

QVariantMap ShellSessionController::synthesizeViewState(
    const QString &sourceMode,
    const QString &viewState,
    const QString &message,
    bool retryable,
    const QString &requestedPacketId)
{
    const QString bannerKind = viewState == QStringLiteral("storage_empty")
        ? QStringLiteral("info")
        : (viewState == QStringLiteral("stale_view") ? QStringLiteral("warning")
                                                     : QStringLiteral("error"));
    QVariantMap viewModel {
        {QStringLiteral("protocol_version"), QStringLiteral("1.0")},
        {QStringLiteral("dto_version"), QStringLiteral("1.0")},
        {QStringLiteral("service_version"), QStringLiteral("qt-host")},
        {QStringLiteral("source_mode"), sourceMode},
        {QStringLiteral("view_state"), viewState},
        {QStringLiteral("view_revision"), QString()},
        {QStringLiteral("interaction_mode"), QStringLiteral("display_only")},
        {QStringLiteral("active_packet_id"), QVariant()},
        {QStringLiteral("requested_packet_id"), requestedPacketId.isEmpty() ? QVariant() : requestedPacketId},
        {QStringLiteral("selection_reason"),
            viewState == QStringLiteral("packet_missing")
                ? QStringLiteral("requested_packet_missing")
                : QStringLiteral("no_packet_available")},
        {QStringLiteral("frame"), defaultFrame()},
        {QStringLiteral("status"), QVariantMap {
             {QStringLiteral("title"), QStringLiteral("Shell unavailable")},
             {QStringLiteral("detail"), viewState},
             {QStringLiteral("source_label"), sourceMode},
             {QStringLiteral("last_updated_at"), QString()},
             {QStringLiteral("queue_status"), QVariant()},
             {QStringLiteral("assigned_reviewer"), QVariant()},
             {QStringLiteral("reviewer_identity"), missingReviewerIdentity()},
             {QStringLiteral("last_action_receipt"), QVariant()},
         }},
        {QStringLiteral("tabs"), QVariantMap {
             {QStringLiteral("active_tab_id"), QStringLiteral("review")},
             {QStringLiteral("items"), QVariantList {
                 QVariantMap {{QStringLiteral("tab_id"), QStringLiteral("queue")},
                     {QStringLiteral("title"), QStringLiteral("Queue")},
                     {QStringLiteral("selected"), false},
                     {QStringLiteral("visible"), true}},
                 QVariantMap {{QStringLiteral("tab_id"), QStringLiteral("review")},
                     {QStringLiteral("title"), QStringLiteral("Review")},
                     {QStringLiteral("selected"), true},
                     {QStringLiteral("visible"), true}},
                 QVariantMap {{QStringLiteral("tab_id"), QStringLiteral("lineage")},
                     {QStringLiteral("title"), QStringLiteral("Lineage")},
                     {QStringLiteral("selected"), false},
                     {QStringLiteral("visible"), true}},
                 QVariantMap {{QStringLiteral("tab_id"), QStringLiteral("events")},
                     {QStringLiteral("title"), QStringLiteral("Events")},
                     {QStringLiteral("selected"), false},
                     {QStringLiteral("visible"), true}},
             }},
         }},
        {QStringLiteral("gate"), unavailableGate(viewState)},
        {QStringLiteral("left_rail"), QVariantMap {
             {QStringLiteral("pane_id"), QStringLiteral("left_rail")},
             {QStringLiteral("title"), QStringLiteral("Review Queue")},
             {QStringLiteral("component_state"), QStringLiteral("error")},
             {QStringLiteral("collapsed"), false},
             {QStringLiteral("visible"), true},
             {QStringLiteral("banner_kind"), bannerKind},
             {QStringLiteral("banner_text"), message},
             {QStringLiteral("rows"), QVariantList()},
         }},
        {QStringLiteral("center_surface"), QVariantMap {
             {QStringLiteral("pane_id"), QStringLiteral("center_surface")},
             {QStringLiteral("title"), QStringLiteral("Packet Surface")},
             {QStringLiteral("component_state"), QStringLiteral("error")},
             {QStringLiteral("collapsed"), false},
             {QStringLiteral("visible"), true},
             {QStringLiteral("banner_kind"), bannerKind},
             {QStringLiteral("banner_text"), message},
             {QStringLiteral("packet_summary"), emptyPacketSummary()},
             {QStringLiteral("validation_summary"), emptyValidationSummary()},
             {QStringLiteral("diff_summary"), emptyDiffSummary()},
         }},
        {QStringLiteral("right_inspector"), QVariantMap {
             {QStringLiteral("pane_id"), QStringLiteral("right_inspector")},
             {QStringLiteral("title"), QStringLiteral("Review Inspector")},
             {QStringLiteral("component_state"), QStringLiteral("error")},
             {QStringLiteral("collapsed"), false},
             {QStringLiteral("visible"), true},
             {QStringLiteral("banner_kind"), bannerKind},
             {QStringLiteral("banner_text"), message},
             {QStringLiteral("evidence_rows"), QVariantList()},
             {QStringLiteral("review_actions"), disabledReviewActions()},
         }},
        {QStringLiteral("bottom_strip"), QVariantMap {
             {QStringLiteral("pane_id"), QStringLiteral("bottom_strip")},
             {QStringLiteral("title"), QStringLiteral("Event Blotter")},
             {QStringLiteral("component_state"), QStringLiteral("error")},
             {QStringLiteral("collapsed"), false},
             {QStringLiteral("visible"), true},
             {QStringLiteral("banner_kind"), bannerKind},
             {QStringLiteral("banner_text"), message},
             {QStringLiteral("event_rows"), QVariantList()},
         }},
        {QStringLiteral("error"), QVariantMap {
             {QStringLiteral("kind"), viewState},
             {QStringLiteral("message"), message},
             {QStringLiteral("retryable"), retryable},
             {QStringLiteral("details"), QString()},
         }},
    };
    viewModel.insert(QStringLiteral("view_revision"), clientViewRevision(viewModel));
    return viewModel;
}

QString ShellSessionController::clientViewRevision(const QVariantMap &viewModel)
{
    QVariantMap canonical = viewModel;
    canonical.remove(QStringLiteral("view_revision"));
    const QByteArray bytes = QJsonDocument::fromVariant(normalizeVariant(canonical)).toJson(QJsonDocument::Compact);
    const QByteArray digest = QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex();
    return QStringLiteral("cli:") + QString::fromLatin1(digest);
}

void ShellSessionController::onClientReady()
{
    requestInitialize();
}

void ShellSessionController::onRpcResult(const QString &method, const QVariantMap &result)
{
    if (method == QStringLiteral("shell.initialize")) {
        if (!containsKeys(result,
                {QStringLiteral("protocol_version"), QStringLiteral("dto_version"),
                    QStringLiteral("service_version"), QStringLiteral("capabilities"),
                    QStringLiteral("interaction_mode"), QStringLiteral("transport"),
                    QStringLiteral("reviewer_identity"), QStringLiteral("review_note_policy")},
                nullptr)) {
            onInvalidReply(QStringLiteral("Shell initialize reply is missing required fields."));
            return;
        }
        const int terminalNoteMinChars = result.value(QStringLiteral("review_note_policy"))
                                             .toMap()
                                             .value(QStringLiteral("terminal_note_min_chars"))
                                             .toInt();
        if (m_terminalNoteMinChars != terminalNoteMinChars) {
            m_terminalNoteMinChars = terminalNoteMinChars;
            emit terminalNoteMinCharsChanged();
        }
        m_initialized = true;
        requestCurrentView(false);
        if (m_sourceMode == QStringLiteral("storage")) {
            m_refreshTimer.start();
        }
        return;
    }
    if (isMutationMethod(method)) {
        setPendingAction(QString());
        setActionErrorMessage(QString());
        if (method == QStringLiteral("shell.approve_packet")
            || method == QStringLiteral("shell.reject_packet")
            || method == QStringLiteral("shell.rework_packet")) {
            setRequestedPacketId(QString());
        }
        requestCurrentView(false);
        return;
    }
    QString errorMessage;
    if (!validateShellViewDto(result, &errorMessage)) {
        onInvalidReply(QStringLiteral("Shell view reply failed validation: %1").arg(errorMessage));
        return;
    }
    applyViewModel(result);
}

void ShellSessionController::onRpcError(const QString &method, const QVariantMap &error)
{
    if (isMutationMethod(method)) {
        setPendingAction(QString());
        setActionErrorMessage(mutationErrorMessage(error));
        return;
    }
    const QVariantMap data = error.value(QStringLiteral("data")).toMap();
    const QString message = error.value(QStringLiteral("message")).toString().isEmpty()
        ? QStringLiteral("Shell service returned an error.")
        : error.value(QStringLiteral("message")).toString();
    const bool retryable = data.contains(QStringLiteral("retryable"))
        ? data.value(QStringLiteral("retryable")).toBool()
        : true;
    Q_UNUSED(method);
    applySynthesizedState(QStringLiteral("service_unavailable"), message, retryable, m_requestedPacketId);
}

void ShellSessionController::onInvalidReply(const QString &message)
{
    if (!m_pendingAction.isEmpty()) {
        setPendingAction(QString());
        setActionErrorMessage(message);
        return;
    }
    applySynthesizedState(QStringLiteral("invalid_reply"), message, true, m_requestedPacketId);
}

void ShellSessionController::onTransportError(const QString &message)
{
    if (!m_pendingAction.isEmpty()) {
        setPendingAction(QString());
        setActionErrorMessage(message);
        return;
    }
    applySynthesizedState(QStringLiteral("service_unavailable"), message, true, m_requestedPacketId);
}

void ShellSessionController::requestInitialize()
{
    m_client->sendRequest(QStringLiteral("shell.initialize"), initializeParams());
}

void ShellSessionController::requestCurrentView(bool refreshRequest)
{
    QVariantMap params;
    if (!m_requestedPacketId.isEmpty()) {
        params.insert(QStringLiteral("packet_id"), m_requestedPacketId);
    }
    if (refreshRequest) {
        params.insert(QStringLiteral("last_view_revision"), stringValue(m_viewModel, QStringLiteral("view_revision")));
    }
    const QString method = m_sourceMode == QStringLiteral("fixture")
        ? QStringLiteral("shell.get_fixture_view")
        : (refreshRequest ? QStringLiteral("shell.refresh_storage_view")
                          : QStringLiteral("shell.get_storage_view"));
    m_client->sendRequest(method, params);
}

void ShellSessionController::sendMutationRequest(
    const QString &method,
    const QVariantMap &params,
    const QString &actionName)
{
    if (m_sourceMode != QStringLiteral("storage")) {
        return;
    }
    setPendingAction(actionName);
    m_client->sendRequest(method, params);
}

void ShellSessionController::applyViewModel(const QVariantMap &viewModel)
{
    const QString newRevision = stringValue(viewModel, QStringLiteral("view_revision"));
    const QString currentRevision = stringValue(m_viewModel, QStringLiteral("view_revision"));
    if (!newRevision.isEmpty() && newRevision == currentRevision) {
        return;
    }
    m_viewModel = viewModel;
    const int refreshInterval = m_viewModel.value(QStringLiteral("frame")).toMap()
                                    .value(QStringLiteral("refresh_interval_ms"))
                                    .toInt();
    if (refreshInterval > 0) {
        m_refreshTimer.setInterval(refreshInterval);
    }
    emit viewModelChanged();
}

void ShellSessionController::applySynthesizedState(
    const QString &viewState,
    const QString &message,
    bool retryable,
    const QString &requestedPacketId)
{
    applyViewModel(synthesizeViewState(m_sourceMode, viewState, message, retryable, requestedPacketId));
}

QString ShellSessionController::resolveServiceProgram() const
{
    const QString explicitProgram = qEnvironmentVariable("SKYNET_SHELL_SERVICE_PATH");
    if (!explicitProgram.isEmpty()) {
        return explicitProgram;
    }

    const QDir appDir(QCoreApplication::applicationDirPath());
    const QString directPath = appDir.filePath(QStringLiteral("skynet_shell_service"));
    if (QFileInfo::exists(directPath)) {
        return directPath;
    }
    const QString siblingPath = appDir.filePath(QStringLiteral("../skynet_shell_service"));
    if (QFileInfo::exists(siblingPath)) {
        return siblingPath;
    }
    return QStringLiteral("skynet_shell_service");
}

QVariantMap ShellSessionController::initializeParams() const
{
    const QByteArray reviewerEnv = qgetenv("SKYNET_REVIEWER");
    const QString reviewerName = QString::fromUtf8(reviewerEnv).trimmed();
    return QVariantMap {
        {QStringLiteral("protocol_version"), QStringLiteral("1.0")},
        {QStringLiteral("dto_version"), QStringLiteral("1.0")},
        {QStringLiteral("client_name"), QStringLiteral("skynet_qt_shell")},
        {QStringLiteral("client_version"),
            QCoreApplication::applicationVersion().isEmpty()
                ? QStringLiteral("0.1.0")
                : QCoreApplication::applicationVersion()},
        {QStringLiteral("requested_capabilities"), QVariantList {
             QStringLiteral("fixture_view"),
             QStringLiteral("storage_view"),
             QStringLiteral("poll_refresh"),
             QStringLiteral("display_only"),
             QStringLiteral("fixed_collapse_geometry"),
         }},
        {QStringLiteral("reviewer_name"), reviewerName.isEmpty() ? QVariant() : reviewerName},
    };
}

void ShellSessionController::openNoteDialog(const QString &action, const QString &title)
{
    const QString packetId = currentPacketId();
    if (packetId.isEmpty()) {
        return;
    }
    m_noteDialogPacketId = packetId;
    setNoteDraft(QString());
    setNoteDialogAction(action);
    setNoteDialogTitle(title);
    setNoteDialogOpen(true);
    setActionErrorMessage(QString());
}

void ShellSessionController::closeNoteDialog()
{
    m_noteDialogPacketId.clear();
    setNoteDialogOpen(false);
    setNoteDialogAction(QString());
    setNoteDialogTitle(QString());
    setNoteDraft(QString());
}

void ShellSessionController::setRequestedPacketId(const QString &packetId)
{
    const QString normalized = packetId.trimmed();
    if (m_requestedPacketId == normalized) {
        return;
    }
    m_requestedPacketId = normalized;
    emit requestedPacketIdChanged();
}

void ShellSessionController::setPendingAction(const QString &pendingAction)
{
    if (m_pendingAction == pendingAction) {
        return;
    }
    m_pendingAction = pendingAction;
    emit pendingActionChanged();
}

void ShellSessionController::setActionErrorMessage(const QString &actionErrorMessage)
{
    if (m_actionErrorMessage == actionErrorMessage) {
        return;
    }
    m_actionErrorMessage = actionErrorMessage;
    emit actionErrorMessageChanged();
}

void ShellSessionController::setNoteDialogOpen(bool open)
{
    if (m_noteDialogOpen == open) {
        return;
    }
    m_noteDialogOpen = open;
    emit noteDialogOpenChanged();
}

void ShellSessionController::setNoteDialogAction(const QString &action)
{
    if (m_noteDialogAction == action) {
        return;
    }
    m_noteDialogAction = action;
    emit noteDialogActionChanged();
}

void ShellSessionController::setNoteDialogTitle(const QString &title)
{
    if (m_noteDialogTitle == title) {
        return;
    }
    m_noteDialogTitle = title;
    emit noteDialogTitleChanged();
}

QString ShellSessionController::currentPacketId() const
{
    return m_viewModel.value(QStringLiteral("active_packet_id")).toString();
}

bool ShellSessionController::isMutationMethod(const QString &method)
{
    return method == QStringLiteral("shell.claim_storage_packet")
        || method == QStringLiteral("shell.approve_packet")
        || method == QStringLiteral("shell.reject_packet")
        || method == QStringLiteral("shell.rework_packet");
}

QString ShellSessionController::mutationErrorMessage(const QVariantMap &error) const
{
    const QVariantMap data = error.value(QStringLiteral("data")).toMap();
    const QVariantMap details = data.value(QStringLiteral("details")).toMap();
    const QString reasonKind = details.value(QStringLiteral("reason_kind")).toString();
    if (reasonKind == QStringLiteral("reviewer_identity_missing")) {
        const QString operatorMessage = details.value(QStringLiteral("operator_message")).toString();
        if (!operatorMessage.isEmpty()) {
            return operatorMessage;
        }
    }
    if (reasonKind == QStringLiteral("claimed_by_other_reviewer")) {
        const QString assigned = details.value(QStringLiteral("assigned_reviewer")).toString();
        if (!assigned.isEmpty()) {
            return QStringLiteral("Claimed by %1. Only the assigned reviewer can complete review.").arg(assigned);
        }
    }
    if (reasonKind == QStringLiteral("terminal_note_too_short")) {
        const int minChars = details.value(QStringLiteral("terminal_note_min_chars")).toInt();
        return QStringLiteral("Review note must be at least %1 characters.").arg(minChars);
    }
    const QString message = error.value(QStringLiteral("message")).toString();
    return message.isEmpty() ? QStringLiteral("Shell service returned an error.") : message;
}

void ShellSessionController::setLeftRailCollapsed(bool collapsed)
{
    if (m_leftRailCollapsed == collapsed) {
        return;
    }
    m_leftRailCollapsed = collapsed;
    emit leftRailCollapsedChanged();
}

void ShellSessionController::setRightInspectorCollapsed(bool collapsed)
{
    if (m_rightInspectorCollapsed == collapsed) {
        return;
    }
    m_rightInspectorCollapsed = collapsed;
    emit rightInspectorCollapsedChanged();
}

void ShellSessionController::setBottomStripCollapsed(bool collapsed)
{
    if (m_bottomStripCollapsed == collapsed) {
        return;
    }
    m_bottomStripCollapsed = collapsed;
    emit bottomStripCollapsedChanged();
}
