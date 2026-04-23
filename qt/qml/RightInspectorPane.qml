import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var pane
    required property bool collapsed
    required property var toggleCollapse
    required property var gate
    required property string pendingAction
    required property string actionErrorMessage
    required property var claimPacket
    required property var approvePacket
    required property var rejectPacket
    required property var reworkPacket

    color: "#151b22"
    border.color: "#2d3945"
    radius: 18

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        RowLayout {
            Layout.fillWidth: true

            Label {
                Layout.fillWidth: true
                text: root.collapsed ? "Inspect" : (root.pane?.title ?? "")
                color: "#f7f2e8"
                font.family: "Avenir Next"
                font.pixelSize: 14
                font.bold: true
            }

            ToolButton {
                text: root.collapsed ? "<" : ">"
                onClicked: root.toggleCollapse()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: !root.collapsed && (root.pane?.banner_text ?? "") !== ""
            color: (root.pane?.banner_kind ?? "") === "warning" ? "#4d3621" : "#23313d"
            radius: 12
            implicitHeight: bannerLabel.implicitHeight + 16

            Label {
                id: bannerLabel
                anchors.fill: parent
                anchors.margins: 8
                wrapMode: Text.WordWrap
                text: root.pane?.banner_text ?? ""
                color: "#f1efe8"
                font.family: "Avenir Next"
                font.pixelSize: 11
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: !root.collapsed
            color: "#202830"
            radius: 14
            implicitHeight: 110
            border.color: "#36414b"

            Column {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 5

                Label {
                    text: "Gate"
                    color: "#f7f2e8"
                    font.family: "Avenir Next"
                    font.pixelSize: 13
                    font.bold: true
                }

                Label {
                    text: root.gate?.label ?? ""
                    color: "#d8b78a"
                    font.family: "Avenir Next"
                    font.pixelSize: 12
                }

                Label {
                    text: "Validation " + (root.gate?.validation_status ?? "") + " | stale " + Boolean(root.gate?.stale)
                    color: "#9db0c3"
                    font.family: "Avenir Next"
                    font.pixelSize: 11
                }
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.collapsed
            clip: true
            model: root.pane?.evidence_rows ?? []
            spacing: 8

            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: 96
                radius: 14
                color: "#202830"
                border.color: "#36414b"

                Column {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 4

                    Label {
                        text: modelData.title
                        color: "#f7f2e8"
                        font.family: "Avenir Next"
                        font.pixelSize: 12
                        font.bold: true
                    }

                    Label {
                        text: modelData.excerpt
                        wrapMode: Text.WordWrap
                        color: "#cfd6de"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !root.collapsed
            spacing: 8

            Button {
                Layout.fillWidth: true
                text: root.pendingAction === "claim" ? "Claiming..." : "Claim"
                visible: Boolean(root.pane?.review_actions?.claim_visible)
                enabled: Boolean(root.pane?.review_actions?.claim_enabled) && root.pendingAction === ""
                onClicked: root.claimPacket()
            }

            Button {
                Layout.fillWidth: true
                text: root.pendingAction === "approve" ? "Approving..." : "Approve"
                enabled: Boolean(root.pane?.review_actions?.approve_enabled) && root.pendingAction === ""
                onClicked: root.approvePacket()
            }

            Button {
                Layout.fillWidth: true
                text: root.pendingAction === "reject" ? "Rejecting..." : "Reject"
                enabled: Boolean(root.pane?.review_actions?.reject_enabled) && root.pendingAction === ""
                onClicked: root.rejectPacket()
            }

            Button {
                Layout.fillWidth: true
                text: root.pendingAction === "rework" ? "Sending..." : "Rework"
                enabled: Boolean(root.pane?.review_actions?.rework_enabled) && root.pendingAction === ""
                onClicked: root.reworkPacket()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: !root.collapsed
                     && ((root.pane?.review_actions?.disabled_reason ?? "") !== ""
                         || root.actionErrorMessage !== "")
            color: (root.actionErrorMessage !== "") ? "#4c2f2b" : "#23313d"
            radius: 12
            implicitHeight: actionMessageLabel.implicitHeight + 16

            Label {
                id: actionMessageLabel
                anchors.fill: parent
                anchors.margins: 8
                wrapMode: Text.WordWrap
                text: root.actionErrorMessage !== ""
                    ? root.actionErrorMessage
                    : (root.pane?.review_actions?.disabled_reason ?? "")
                color: "#f1efe8"
                font.family: "Avenir Next"
                font.pixelSize: 11
            }
        }
    }
}
