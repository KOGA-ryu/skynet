import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var pane

    color: "#171b1f"
    border.color: "#2d3945"
    radius: 18

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Label {
            text: root.pane?.title ?? ""
            color: "#f7f2e8"
            font.family: "Avenir Next"
            font.pixelSize: 16
            font.bold: true
        }

        Rectangle {
            Layout.fillWidth: true
            visible: (root.pane?.banner_text ?? "") !== ""
            color: (root.pane?.banner_kind ?? "") === "warning" ? "#4d3621"
                                                                  : ((root.pane?.banner_kind ?? "") === "error" ? "#4c2f2b" : "#23313d")
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
            radius: 16
            color: "#222831"
            border.color: "#36414b"
            implicitHeight: 128

            Column {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 6

                Label {
                    text: root.pane?.packet_summary?.title ?? ""
                    color: "#f7f2e8"
                    font.family: "Avenir Next"
                    font.pixelSize: 18
                    font.bold: true
                }

                Label {
                    text: "Packet " + (root.pane?.packet_summary?.packet_id ?? "")
                    color: "#9db0c3"
                    font.family: "Avenir Next"
                    font.pixelSize: 11
                }

                Label {
                    text: "Subject " + (root.pane?.packet_summary?.subject_label ?? "")
                    color: "#d8b78a"
                    font.family: "Avenir Next"
                    font.pixelSize: 11
                }

                Label {
                    text: "Render " + (root.pane?.packet_summary?.render_status ?? "")
                    color: "#cfd6de"
                    font.family: "IBM Plex Sans"
                    font.pixelSize: 11
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 16
                color: "#202830"
                border.color: "#36414b"

                Column {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 6

                    Label {
                        text: "Validation"
                        color: "#f7f2e8"
                        font.family: "Avenir Next"
                        font.pixelSize: 14
                        font.bold: true
                    }

                    Label {
                        text: (root.pane?.validation_summary?.status ?? "") + " | blockers " + Number(root.pane?.validation_summary?.blocker_count ?? 0)
                        color: "#d8b78a"
                        font.family: "Avenir Next"
                        font.pixelSize: 12
                    }

                    Label {
                        text: "Issues " + Number(root.pane?.validation_summary?.issue_count ?? 0)
                        color: "#9db0c3"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 16
                color: "#202830"
                border.color: "#36414b"

                Column {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 6

                    Label {
                        text: "Diff"
                        color: "#f7f2e8"
                        font.family: "Avenir Next"
                        font.pixelSize: 14
                        font.bold: true
                    }

                    Label {
                        text: (root.pane?.diff_summary?.summary ?? "")
                        color: "#cfd6de"
                        wrapMode: Text.WordWrap
                        font.family: "Avenir Next"
                        font.pixelSize: 12
                    }

                    Label {
                        text: "Changes " + Number(root.pane?.diff_summary?.change_count ?? 0)
                        color: "#9db0c3"
                        font.family: "IBM Plex Sans"
                        font.pixelSize: 11
                    }
                }
            }
        }
    }
}
