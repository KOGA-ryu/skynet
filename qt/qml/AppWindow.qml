import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5
import SkynetShell 1.0

ApplicationWindow {
    id: root

    visible: true
    title: "Skynet Shell"
    color: "#0d1116"

    readonly property var model: shellSession.viewModel
    readonly property var frame: model?.frame ?? {}
    readonly property bool terminalNoteValid: shellSession.noteDialogAction === "approve"
        || (shellSession.noteDraft.trim().length >= shellSession.terminalNoteMinChars)

    minimumWidth: geometry.appMinWidth
    minimumHeight: geometry.appMinHeight
    width: Math.max(geometry.appMinWidth, 1520)
    height: Math.max(geometry.appMinHeight, 980)

    ShellGeometryState {
        id: geometry
        frame: root.frame
        leftRailCollapsed: shellSession.leftRailCollapsed
        rightInspectorCollapsed: shellSession.rightInspectorCollapsed
        bottomStripCollapsed: shellSession.bottomStripCollapsed
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#111821" }
            GradientStop { position: 0.55; color: "#0d1116" }
            GradientStop { position: 1.0; color: "#19160f" }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        StatusStrip {
            Layout.fillWidth: true
            Layout.preferredHeight: geometry.statusStripHeight + 12
            status: root.model?.status ?? {}
            gate: root.model?.gate ?? {}
        }

        TabRail {
            Layout.fillWidth: true
            Layout.preferredHeight: geometry.tabRailHeight + 12
            tabs: root.model?.tabs ?? {}
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            LeftControlRail {
                Layout.preferredWidth: geometry.leftRailWidth
                Layout.fillHeight: true
                pane: root.model?.left_rail ?? {}
                collapsed: shellSession.leftRailCollapsed
                toggleCollapse: function() { shellSession.toggleLeftRailCollapsed() }
                activePacketId: root.model?.active_packet_id ?? ""
                requestedPacketId: shellSession.requestedPacketId
                selectPacket: function(packetId) { shellSession.selectPacket(packetId) }
            }

            CenterPacketPane {
                Layout.fillWidth: true
                Layout.fillHeight: true
                pane: root.model?.center_surface ?? {}
            }

            RightInspectorPane {
                Layout.preferredWidth: geometry.rightInspectorWidth
                Layout.fillHeight: true
                pane: root.model?.right_inspector ?? {}
                collapsed: shellSession.rightInspectorCollapsed
                toggleCollapse: function() { shellSession.toggleRightInspectorCollapsed() }
                gate: root.model?.gate ?? {}
                pendingAction: shellSession.pendingAction
                actionErrorMessage: shellSession.actionErrorMessage
                claimPacket: function() { shellSession.claimPacket() }
                approvePacket: function() { shellSession.approvePacket() }
                rejectPacket: function() { shellSession.rejectPacket() }
                reworkPacket: function() { shellSession.reworkPacket() }
            }
        }

        BottomBlotterPane {
            Layout.fillWidth: true
            Layout.preferredHeight: geometry.bottomStripHeight
            pane: root.model?.bottom_strip ?? {}
            collapsed: shellSession.bottomStripCollapsed
            toggleCollapse: function() { shellSession.toggleBottomStripCollapsed() }
        }
    }

    Dialog {
        id: noteDialog
        modal: true
        visible: shellSession.noteDialogOpen
        title: shellSession.noteDialogTitle
        width: Math.min(root.width - 80, 520)
        anchors.centerIn: parent
        closePolicy: Popup.NoAutoClose

        contentItem: ColumnLayout {
            spacing: 10

            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: shellSession.noteDialogAction === "approve"
                    ? "Approval note is optional. Leave it blank to send null."
                    : "A review note is required and must be at least "
                        + shellSession.terminalNoteMinChars
                        + " characters."
                color: "#d8b78a"
                font.family: "Avenir Next"
                font.pixelSize: 12
            }

            TextArea {
                Layout.fillWidth: true
                Layout.preferredHeight: 180
                wrapMode: TextEdit.Wrap
                color: "#f1efe8"
                placeholderText: shellSession.noteDialogAction === "approve"
                    ? "Optional note"
                    : "Required review note"
                text: shellSession.noteDraft
                onTextChanged: shellSession.noteDraft = text
                background: Rectangle {
                    color: "#151b22"
                    border.color: "#36414b"
                    radius: 12
                }
            }

            Label {
                Layout.fillWidth: true
                visible: shellSession.noteDialogAction !== "approve"
                text: "Characters " + shellSession.noteDraft.trim().length + " / "
                    + shellSession.terminalNoteMinChars
                color: root.terminalNoteValid ? "#9db0c3" : "#d87f72"
                font.family: "Avenir Next"
                font.pixelSize: 11
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Item {
                    Layout.fillWidth: true
                }

                Button {
                    text: "Cancel"
                    onClicked: shellSession.cancelPendingReviewAction()
                }

                Button {
                    text: shellSession.noteDialogAction === "approve"
                        ? "Approve"
                        : (shellSession.noteDialogAction === "reject" ? "Reject" : "Rework")
                    enabled: root.terminalNoteValid
                    onClicked: shellSession.submitPendingReviewAction()
                }
            }
        }

        background: Rectangle {
            color: "#10161d"
            border.color: "#2d3945"
            radius: 18
        }
    }
}
