use crate::shell::gate::gate_label;
use crate::shell::ShellSnapshot;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShellRuntimeFrame {
    pub status_strip_height_px: u16,
    pub tab_rail_height_px: u16,
    pub left_rail_width_px: u16,
    pub right_inspector_width_px: u16,
    pub bottom_strip_height_px: u16,
    pub center_min_width_px: u16,
    pub center_min_height_px: u16,
    pub gate_label: &'static str,
}

pub fn runtime_frame(snapshot: &ShellSnapshot) -> ShellRuntimeFrame {
    ShellRuntimeFrame {
        status_strip_height_px: snapshot.layout.status_strip_height_px,
        tab_rail_height_px: snapshot.layout.tab_rail_height_px,
        left_rail_width_px: snapshot.layout.left_rail.default_px,
        right_inspector_width_px: snapshot.layout.right_inspector.default_px,
        bottom_strip_height_px: snapshot.layout.bottom_strip.default_px,
        center_min_width_px: snapshot.layout.center_min_width_px,
        center_min_height_px: snapshot.layout.center_min_height_px,
        gate_label: gate_label(&snapshot.fixtures.gate),
    }
}
