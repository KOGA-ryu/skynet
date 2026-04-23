use crate::shell::api::ShellViewDto;

pub fn render_text_screen(model: &ShellViewDto) -> String {
    let mut out = Vec::new();
    out.push(format!(
        "SHELL {} {} {}",
        model.source_mode, model.view_state, model.view_revision
    ));
    out.push(format!(
        "STATUS {} | {} | {}",
        model.status.title, model.status.detail, model.gate.label
    ));
    out.push(format!(
        "LEFT {} rows={}",
        model.left_rail.title,
        model.left_rail.rows.len()
    ));
    out.push(format!(
        "CENTER {} packet={}",
        model.center_surface.title, model.center_surface.packet_summary.packet_id
    ));
    out.push(format!(
        "RIGHT {} evidence={}",
        model.right_inspector.title,
        model.right_inspector.evidence_rows.len()
    ));
    out.push(format!(
        "BOTTOM {} events={}",
        model.bottom_strip.title,
        model.bottom_strip.event_rows.len()
    ));
    out.join("\n")
}
