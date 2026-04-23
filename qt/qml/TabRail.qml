import QtQuick 6.5
import QtQuick.Controls 6.5
import QtQuick.Layouts 6.5

Rectangle {
    id: root

    required property var tabs

    color: "#101419"
    border.color: "#2d3945"
    radius: 14

    RowLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        Repeater {
            model: root.tabs?.items ?? []

            delegate: Rectangle {
                required property var modelData
                visible: Boolean(modelData.visible)
                radius: 10
                color: Boolean(modelData.selected) ? "#d86f2d" : "#202830"
                implicitWidth: tabLabel.implicitWidth + 22
                implicitHeight: 32

                Label {
                    id: tabLabel
                    anchors.centerIn: parent
                    text: modelData.title
                    color: "#f7f2e8"
                    font.family: "Avenir Next"
                    font.pixelSize: 12
                    font.bold: Boolean(modelData.selected)
                }
            }
        }
    }
}
