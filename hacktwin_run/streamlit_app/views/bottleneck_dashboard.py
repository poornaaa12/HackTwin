"""
streamlit_app/views/bottleneck_dashboard.py

5. Bottleneck Dashboard View
Displays PC hotspot breakdown, loop iteration counters, register churn gauges, and root cause findings.
"""

from bottleneck_analysis import analyze_run_telemetry
import matplotlib.pyplot as plt
import pandas as pd


def render_bottleneck_dashboard(st_module, loader):
    """Render Bottleneck Dashboard section."""
    st_module.header("5. Bottleneck & Root-Cause Dashboard")
    st_module.write("Identifies PC execution hotspots, backward loop jumps, register churn bottlenecks, and root causes.")

    runs_df = loader.get_runs_df()
    if runs_df.empty:
        st_module.warning("No runs available.")
        return

    selected_run = st_module.selectbox("Select Simulation Run:", runs_df["run_id"].unique(), key="bottleneck_dash_run")
    telem = loader.store.get_run_telemetry(selected_run)
    if not telem:
        st_module.warning("Run telemetry not found.")
        return

    analysis = analyze_run_telemetry(telem)

    col1, col2 = st_module.columns(2)

    with col1:
        st_module.subheader("Top PC Execution Hotspots")
        hotspots = analysis.get("pc_hotspots", [])
        if hotspots:
            hs_df = pd.DataFrame(hotspots)
            st_module.dataframe(hs_df)

            fig, ax = plt.subplots(figsize=(5, 3))
            ax.bar([f"PC {h['pc']}" for h in hotspots], [h["pct_cycles"] for h in hotspots], color="#EF4444")
            ax.set_ylabel("Cycle Share (%)")
            ax.set_title("Hotspot Cycle Share %")
            plt.tight_layout()
            st_module.pyplot(fig)
            plt.close(fig)
        else:
            st_module.info("No hotspots detected.")

    with col2:
        st_module.subheader("Detected Loops & Backward Jumps")
        loops = analysis.get("detected_loops", [])
        if loops:
            lp_df = pd.DataFrame(loops)
            st_module.dataframe(lp_df)
        else:
            st_module.info("No loops detected.")

    st_module.subheader("Root-Cause Diagnostic Alerts")
    findings = analysis.get("root_cause_diagnostics", [])
    for diag in findings:
        sev = diag["severity"]
        msg = f"**[{sev}] ({diag['category']})** {diag['issue']}  \n*Recommendation:* {diag['recommendation']}"
        if sev == "HIGH":
            st_module.error(msg)
        elif sev == "MEDIUM":
            st_module.warning(msg)
        else:
            st_module.info(msg)
