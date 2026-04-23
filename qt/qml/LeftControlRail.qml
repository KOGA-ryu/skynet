import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var pane
    required property bool collapsed
    required property var toggleCollapse
    required property string activePacketId
    required property string requestedPacketId
    required property var selectPacket

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
                text: root.collapsed ? "Queue" : (root.pane?.title ?? "")
                color: "#f7f2e8"
                font.family: "Avenir Next"
                font.pixelSize: 14
                font.bold: true
                elide: Text.ElideRight
            }

            ToolButton {
                text: root.collapsed ? ">" : "<"
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
            model: root.pane?.rows ?? []
            spacing: 8

            delegate: Rectangle {
                required property var modelData
                readonly property bool selected: (root.requestedPacketId !== ""
                        ? root.requestedPacketId === modelData.packet_id
                        : root.activePacketId === modelData.packet_id)
                width: ListView.view.width
                height: 82
                radius: 14
                color: selected ? "#2c3744" : (Boolean(modelData.stale) ? "#3f2a2a" : "#202830")
                border.color: selected ? "#d8b78a" : "#36414b"
                border.width: selected ? 2 : 1

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
                        elide: Text.ElideRight
                    }

                    Label {
                        text: modelData.packet_id
                        color: "#9db0c3"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                    }

                    Label {
                        text: modelData.queue_status + " | validation " + modelData.validation_status
                        color: "#d8b78a"
                        font.family: "Avenir Next"
                        font.pixelSize: 11
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectPacket(modelData.packet_id)
                }
            }
        }
    }
}
