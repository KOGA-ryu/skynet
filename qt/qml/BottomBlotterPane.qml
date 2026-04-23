import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var pane
    required property bool collapsed
    required property var toggleCollapse

    color: "#13181d"
    border.color: "#2d3945"
    radius: 18

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true

            Label {
                Layout.fillWidth: true
                text: root.collapsed ? "Events" : (root.pane?.title ?? "")
                color: "#f7f2e8"
                font.family: "Avenir Next"
                font.pixelSize: 14
                font.bold: true
            }

            ToolButton {
                text: root.collapsed ? "^" : "v"
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

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.collapsed
            clip: true
            model: root.pane?.event_rows ?? []
            spacing: 6

            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: 40
                radius: 10
                color: "#202830"

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 10

                    Label {
                        text: modelData.severity
                        color: modelData.severity === "warning" ? "#d8b78a" : "#9db0c3"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                        font.bold: true
                    }

                    Label {
                        Layout.fillWidth: true
                        text: modelData.message
                        color: "#f7f2e8"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }

                    Label {
                        text: modelData.timestamp
                        color: "#8193a5"
                        font.family: "Avenir Next"
                        font.pixelSize: 10
                    }
                }
            }
        }
    }
}
