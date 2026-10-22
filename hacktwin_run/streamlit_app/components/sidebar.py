"""
streamlit_app/components/sidebar.py

Sidebar navigation and database path configuration component.
"""

def render_sidebar(st_module) -> str:
    """Render sidebar navigation menu and return active view selection."""
    st_module.sidebar.title("HackTwin")
    st_module.sidebar.caption("HDL Hardware Digital Twin Dashboard")

    view_options = [
        "1. Run Timeline Visualization",
        "2. Instruction Execution Timeline",
        "3. Register Activity Graphs",
        "4. Data Memory Heatmaps",
        "5. Bottleneck & Root-Cause Dashboard",
        "6. Design Comparison & Ranking Charts"
    ]

    selected_view = st_module.sidebar.radio("Navigation View:", view_options)
    st_module.sidebar.divider()
    st_module.sidebar.info("Twin Store DB: `data/hacktwin.db`")
    return selected_view
