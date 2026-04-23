use crate::shell::{RailSummary, ShellSnapshot};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimePaneSet {
    pub left: RailSummary,
    pub center: RailSummary,
    pub right: RailSummary,
    pub bottom: RailSummary,
}

pub fn runtime_panes(snapshot: &ShellSnapshot) -> RuntimePaneSet {
    RuntimePaneSet {
        left: snapshot.left_rail.clone(),
        center: snapshot.center_surface.clone(),
        right: snapshot.right_inspector.clone(),
        bottom: snapshot.bottom_strip.clone(),
    }
}
