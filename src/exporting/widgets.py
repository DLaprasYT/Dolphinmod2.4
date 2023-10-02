"""Write config group values to the config."""
from exporting import STEPS
from packages.widgets import CONFIG, ConfigGroup
import packages


@STEPS.add_step(prereq=[], results=[])
async def export(exp_data: packages.ExportData) -> None:
    """Write all our values to the config."""
    for conf in exp_data.packset.all_obj(ConfigGroup):
        config_section = CONFIG[conf.id]
        for s_wid in conf.widgets:
            if s_wid.has_values:
                config_section[s_wid.id] = s_wid.value
        for m_wid in conf.multi_widgets:
            for num, value in m_wid.values.items():
                config_section[f'{m_wid.id}_{num}'] = value
        if not config_section:
            del CONFIG[conf.id]
    CONFIG.save_check()
