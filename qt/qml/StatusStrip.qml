import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var status
    required property var gate

    color: "#181d23"
    border.color: "#2d3945"
    radius: 14

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: root.status?.title ?? ""
                    color: "#f1efe8"
                    font.family: "Avenir Next"
                    font.pixelSize: 18
                    font.bold: true
                }

                Label {
                    text: {
                        const reviewerName = root.status?.reviewer_identity?.reviewer_name ?? ""
                        const queueStatus = root.status?.queue_status ?? ""
                        const assignedReviewer = root.status?.assigned_reviewer ?? ""
                        let detail = root.status?.detail ?? ""
                        if (queueStatus !== "")
                            detail += " | queue " + queueStatus
                        if (assignedReviewer !== "")
                            detail += " | assigned " + assignedReviewer
                        if (reviewerName !== "")
                            detail += " | session " + reviewerName
                        return detail
                    }
                    color: "#9db0c3"
                    font.family: "Avenir Next"
                    font.pixelSize: 12
                }
            }

            Rectangle {
                color: root.gate?.approve_enabled ? "#244e3b" : "#4c2f2b"
                radius: 10
                implicitWidth: gateLabel.implicitWidth + 20
                implicitHeight: 32

                Label {
                    id: gateLabel
                    anchors.centerIn: parent
                    text: root.gate?.label ?? ""
                    color: "#f7f2e8"
                    font.family: "Avenir Next"
                    font.pixelSize: 12
                    font.bold: true
                }
            }

            Label {
                text: "Blockers " + Number(root.gate?.blocker_count ?? 0)
                color: "#d8b78a"
                font.family: "Avenir Next"
                font.pixelSize: 12
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: root.status?.last_action_receipt !== null
                     && root.status?.last_action_receipt !== undefined
            color: "#23313d"
            radius: 10
            implicitHeight: receiptLabel.implicitHeight + 14

            Label {
                id: receiptLabel
                anchors.fill: parent
                anchors.margins: 7
                wrapMode: Text.WordWrap
                text: {
                    const receipt = root.status?.last_action_receipt
                    if (!receipt)
                        return ""
                    return "Last action: "
                        + receipt.decision
                        + " "
                        + receipt.packet_id
                        + " by "
                        + receipt.reviewer
                        + " at "
                        + receipt.timestamp
                }
                color: "#f1efe8"
                font.family: "Avenir Next"
                font.pixelSize: 11
            }
        }
    }
}
