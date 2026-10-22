"""
streamlit_app/components/metrics_header.py

Top KPI header summary component.
"""

def render_metrics_header(st_module, loader):
    """Render top summary metrics cards."""
    try:
        d_count = loader.store.design_count()
        r_count = loader.store.run_count()
        c_count = loader.store.channel_count()

        col1, col2, col3 = st_module.columns(3)
        col1.metric("Registered Designs", f"{d_count} Designs")
        col2.metric("Simulation Runs", f"{r_count} Runs")
        col3.metric("Telemetry Channels", f"{c_count} Streams")
        st_module.divider()
    except Exception as e:
        st_module.error(f"Error loading header metrics: {e}")
