"""Customizable configuration for specific items or groups of them."""
from typing import (
    Generic, Iterable, Optional, Callable, List, Tuple, Dict, Set, Iterator, AsyncIterator,
    Awaitable, Type, TypeVar, Protocol, Union
)
from typing_extensions import Self, TypeAlias
from tkinter import ttk
import tkinter as tk
import itertools
import functools

from srctools import EmptyMapping, Keyvalues, Vec, logger
import trio
import attrs

from app import (
    TK_ROOT, UI, background_run, localisation, signage_ui, tkMarkdown, sound, tk_tools,
    StyleVarPane,
)
from app.tooltip import add_tooltip
from config.widgets import WidgetConfig
from app.localisation import TransToken, TransTokenSource
import BEE2_config
import config
import utils
import packages
from ..SubPane import SubPane


LOGGER = logger.get_logger(__name__)


class ConfigProto(Protocol):
    """Protocol widget configuration classes must match."""
    @classmethod
    def parse(cls, conf: Keyvalues, /) -> Self: ...


ConfT = TypeVar('ConfT', bound=ConfigProto)  # Type of the config object for a widget.
OptConfT = TypeVar('OptConfT', bound=Optional[ConfigProto])


@attrs.frozen
class WidgetType:
    """Information about a type of widget."""
    name: str
    is_wide: bool


@attrs.frozen
class WidgetTypeWithConf(WidgetType, Generic[ConfT]):
    """Information about a type of widget, that requires configuration."""
    conf_type: Type[ConfT]

# Maps widget type names to the type info.
WIDGET_KINDS: Dict[str, WidgetType] = {}
CLS_TO_KIND: Dict[Type[ConfigProto], WidgetTypeWithConf] = {}

# Called when the var is changed, to update UI if required.
UpdateFunc: TypeAlias = Callable[[str], Awaitable[None]]

# Functions for each widget.
# The function is passed a parent frame, StringVar, and Keyvalues block.
# The widget to be installed should be returned, and a callback to refresh the UI.
# If wide is set, the widget is put into a labelframe, instead of having a label to the side.
SingleCreateFunc: TypeAlias = Callable[
    [tk.Widget, tk.StringVar, OptConfT],
    Awaitable[Tuple[tk.Widget, UpdateFunc]]
]
WidgetLookup: utils.FuncLookup[SingleCreateFunc[Keyvalues]] = utils.FuncLookup('Widgets', attrs=['wide'])

# Override for timer-type widgets to be more compact - passed a num:var dict of StringVars
# instead. The widgets should insert themselves into the parent frame.
# It then yields timer_val, update-func pairs.
MultiCreateFunc: TypeAlias = Callable[
    [tk.Widget, List[Tuple[str, tk.StringVar]], OptConfT],
    AsyncIterator[Tuple[str, UpdateFunc]]
]
WidgetLookupMulti: utils.FuncLookup[MultiCreateFunc[Keyvalues]] = utils.FuncLookup('Multi-Widgets')

# The functions registered for each.
_UI_IMPL_SINGLE: dict[WidgetType, SingleCreateFunc] = {}
_UI_IMPL_MULTI: dict[WidgetType, MultiCreateFunc] = {}

CONFIG = BEE2_config.ConfigFile('item_cust_configs.cfg')

TIMER_NUM = list(map(str, range(3, 31)))
TIMER_NUM_INF = ['inf', *TIMER_NUM]

INF = TransToken.untranslated('∞')
# i18n: The format for timer numerals.
_TIMER_TRANS = TransToken.ui('{timer_num:00}')
TIMER_NUM_TRANS = {
    num: _TIMER_TRANS.format(timer_num=num)
    for num in TIMER_NUM
}
del _TIMER_TRANS
TIMER_NUM_TRANS['inf'] = INF
TRANS_COLON = TransToken.untranslated('{text}: ')
TRANS_GROUP_HEADER = TransToken.ui('{name} ({page}/{count})')  # i18n: Header layout for Item Properties pane.
# For the item-variant widget, we need to refresh on style changes.
ITEM_VARIANT_LOAD: List[Tuple[str, Callable[[], object]]] = []

window: Optional[SubPane] = None


def register(*names: str, wide: bool=False) -> Callable[[Type[ConfT]], Type[ConfT]]:
    """Register a widget type that takes config.

    If wide is set, the widget is put into a labelframe, instead of having a label to the side.
    """
    if not names:
        raise TypeError('No name defined!')

    def deco(cls: Type[ConfT]) -> Type[ConfT]:
        """Do the registration."""
        kind = WidgetTypeWithConf(names[0], wide, cls)
        assert cls not in CLS_TO_KIND, cls
        CLS_TO_KIND[cls] = kind
        for name in names:
            name = name.casefold()
            assert name not in WIDGET_KINDS, name
            WIDGET_KINDS[name] = kind
        return cls
    return deco


def register_no_conf(*names: str, wide: bool=False) -> WidgetType:
    """Register a widget type which does not need additional configuration.

    Many only need the default values.
    """
    kind = WidgetType(names[0], wide)
    for name in names:
        name = name.casefold()
        assert name not in WIDGET_KINDS, name
        WIDGET_KINDS[name] = kind
    return kind


def ui_single_wconf(cls: Type[ConfT]) -> Callable[[SingleCreateFunc[ConfT]], SingleCreateFunc[ConfT]]:
    """Register the UI function used for singular widgets with configs."""
    kind = CLS_TO_KIND[cls]
    def deco(func: SingleCreateFunc[ConfT]) -> SingleCreateFunc[ConfT]:
        """Do the registration."""
        _UI_IMPL_SINGLE[kind] = func
        return func
    return deco


def ui_single_no_conf(kind: WidgetType) -> Callable[[SingleCreateFunc[None]], SingleCreateFunc[None]]:
    """Register the UI function used for singular widgets without configs."""
    def deco(func: SingleCreateFunc[None]) -> SingleCreateFunc[None]:
        """Do the registration."""
        if isinstance(kind, WidgetTypeWithConf):
            raise TypeError('Widget type has config, but singular function does not!')
        _UI_IMPL_SINGLE[kind] = func
        return func
    return deco


def ui_multi_wconf(cls: Type[ConfT]) -> Callable[[MultiCreateFunc[ConfT]], MultiCreateFunc[ConfT]]:
    """Register the UI function used for multi widgets with configs."""
    kind = CLS_TO_KIND[cls]
    def deco(func: MultiCreateFunc[ConfT]) -> MultiCreateFunc[ConfT]:
        """Do the registration."""
        _UI_IMPL_MULTI[kind] = func
        return func
    return deco


def ui_multi_no_conf(kind: WidgetType) -> Callable[[MultiCreateFunc[None]], MultiCreateFunc[None]]:
    """Register the UI function used for multi widgets without configs."""
    def deco(func: MultiCreateFunc[None]) -> MultiCreateFunc[None]:
        """Do the registration."""
        if isinstance(kind, WidgetTypeWithConf):
            raise TypeError('Widget type has config, but multi function does not!')
        _UI_IMPL_MULTI[kind] = func
        return func
    return deco


async def nop_update(__value: str) -> None:
    """Placeholder callback which does nothing."""


def parse_color(color: str) -> Tuple[int, int, int]:
    """Parse a string into a color."""
    if color.startswith('#'):
        try:
            r = int(color[1:3], base=16)
            g = int(color[3:5], base=16)
            b = int(color[5:], base=16)
        except ValueError:
            LOGGER.warning('Invalid RGB value: "{}"!', color)
            r = g = b = 128
    else:
        r, g, b = map(int, Vec.from_str(color, 128, 128, 128))
    return r, g, b


@attrs.define
class Widget:
    """Common logic for both kinds of widget that can appear on a ConfigGroup."""
    group_id: str
    id: str
    name: TransToken
    tooltip: TransToken
    config: object
    kind: WidgetType

    @property
    def has_values(self) -> bool:
        """Item variant widgets don't have configuration, all others do."""
        return self.kind is not KIND_ITEM_VARIANT


@attrs.define
class SingleWidget(Widget):
    """Represents a single widget with no timer value."""
    value: tk.StringVar
    ui_cback: Optional[UpdateFunc] = None

    async def apply_conf(self, data: WidgetConfig) -> None:
        """Apply the configuration to the UI."""
        if isinstance(data.values, str):
            self.value.set(data.values)
        else:
            LOGGER.warning('{}:{}: Saved config is timer-based, but widget is singular.', self.group_id, self.id)

    def __attrs_post_init__(self) -> None:
        """Add functions to recompute state/UI when changed."""
        save_id = f'{self.group_id}:{self.id}'

        def on_changed(*_) -> None:
            """Recompute state and UI when changed."""
            val = self.value.get()
            config.APP.store_conf(WidgetConfig(val), save_id)
            if self.ui_cback is not None:
                background_run(self.ui_cback, val)

        self.value.trace_add('write', on_changed)


@attrs.define
class MultiWidget(Widget):
    """Represents a group of multiple widgets for all the timer values."""
    use_inf: bool  # For timer, is infinite valid?
    values: List[Tuple[str, tk.StringVar]]
    ui_cbacks: Dict[str, UpdateFunc] = attrs.Factory(dict)

    async def apply_conf(self, data: WidgetConfig) -> None:
        """Apply the configuration to the UI."""
        if isinstance(data.values, str):
            # Single in conf, apply to all.
            for _, var in self.values:
                var.set(data.values)
        else:
            for tim_val, var in self.values:
                try:
                    val = data.values[tim_val]
                except KeyError:
                    continue
                var.set(val)

    def __attrs_post_init__(self) -> None:
        """Add functions to recompute state/UI when changed."""
        for tim_val, var in self.values:
            var.trace_add('write', self._get_on_changed(tim_val, var))

    def _get_on_changed(self, tim_val: str, var: tk.StringVar) -> Callable[[str, str, str], None]:
        """Get a function to recompute state and UI when changed."""
        save_id = f'{self.group_id}:{self.id}'

        def on_changed(*_) -> None:
            """Recompute state and UI when changed."""
            try:
                cback = self.ui_cbacks[tim_val]
            except KeyError:
                pass
            else:
                background_run(cback, var.get())
            config.APP.store_conf(WidgetConfig({
                num: sub_var.get()
                for num, sub_var in self.values
            }), save_id)
        return on_changed


class ConfigGroup(packages.PakObject, allow_mult=True, needs_foreground=True):
    """A group of configs for an item."""
    def __init__(
        self,
        conf_id: str,
        group_name: TransToken,
        desc,
        widgets: List[SingleWidget],
        multi_widgets: List[MultiWidget],
    ) -> None:
        self.id = conf_id
        self.name = group_name
        self.desc = desc
        self.widgets = widgets
        self.multi_widgets = multi_widgets

        self.ui_frame: Optional[ttk.Frame] = None
        # If true, the create_widgets method has been called.
        self.creating = False

    @classmethod
    async def parse(cls, data: packages.ParseData) -> 'ConfigGroup':
        """Parse the config group from info.txt."""
        props = data.info

        if data.is_override:
            # Override doesn't have a name
            group_name = TransToken.BLANK
        else:
            group_name = TransToken.parse(data.pak_id, props['Name'])

        desc = packages.desc_parse(props, data.id, data.pak_id)

        widgets: list[SingleWidget] = []
        multi_widgets: list[MultiWidget] = []

        for wid in props.find_all('Widget'):
            await trio.sleep(0)
            try:
                kind = WIDGET_KINDS[wid['type'].casefold()]
            except KeyError:
                LOGGER.warning(
                    'Unknown widget type "{}" in <{}:{}>!',
                    wid['type'],
                    data.pak_id,
                    data.id,
                )
                continue

            is_timer = wid.bool('UseTimer')
            use_inf = is_timer and wid.bool('HasInf')
            wid_id = wid['id'].casefold()
            try:
                name = TransToken.parse(data.pak_id, wid['Label'])
            except LookupError:
                name = TransToken.untranslated(wid_id)
            tooltip = TransToken.parse(data.pak_id, wid['Tooltip', ''])
            default_prop = wid.find_key('Default', '')
            values: list[tuple[str, tk.StringVar]]

            conf = config.APP.get_cur_conf(WidgetConfig, f'{data.id}:{wid_id}', default=WidgetConfig())

            # Special case - can't be timer, and no values.
            if kind is KIND_ITEM_VARIANT:
                if is_timer:
                    LOGGER.warning("Item Variants can't be timers! ({}.{})", data.id, wid_id)
                    is_timer = use_inf = False

            if isinstance(kind, WidgetTypeWithConf):
                wid_conf: object = kind.conf_type.parse(wid)
            else:
                wid_conf = None

            if is_timer:
                if default_prop.has_children():
                    defaults = {
                        num: default_prop[num]
                        for num in (TIMER_NUM_INF if use_inf else TIMER_NUM)
                    }
                else:
                    # All the same.
                    defaults = dict.fromkeys(TIMER_NUM_INF if use_inf else TIMER_NUM, default_prop.value)

                values = []
                for num in (TIMER_NUM_INF if use_inf else TIMER_NUM):
                    if conf.values is EmptyMapping:
                        # No new conf, check the old conf.
                        cur_value = CONFIG.get_val(data.id, f'{wid_id}_{num}', defaults[num])
                    elif isinstance(conf.values, str):
                        cur_value = conf.values
                    else:
                        cur_value = conf.values[num]
                    values.append((num, tk.StringVar(
                        value=cur_value,
                        name=f'itemconf_{data.id}_{wid_id}_{num}'
                    )))

                multi_widgets.append(MultiWidget(
                    group_id=data.id,
                    id=wid_id,
                    name=name,
                    tooltip=tooltip,
                    config=wid_conf,
                    kind=kind,
                    values=values,
                    use_inf=use_inf,
                ))
            else:
                # Singular Widget.
                if default_prop.has_children():
                    raise ValueError(
                        f'{data.id}:{wid_id}: Can only have multiple defaults for timer-ed widgets!'
                    )

                if kind is KIND_ITEM_VARIANT:
                    cur_value = ''  # Not used.
                elif conf.values is EmptyMapping:
                    # No new conf, check the old conf.
                    cur_value = CONFIG.get_val(data.id, wid_id, default_prop.value)
                elif isinstance(conf.values, str):
                    cur_value = conf.values
                else:
                    LOGGER.warning('Widget {}:{} had timer defaults, but widget is singular!', data.id, wid_id)
                    cur_value = default_prop.value

                widgets.append(SingleWidget(
                    group_id=data.id,
                    id=wid_id,
                    name=name,
                    tooltip=tooltip,
                    kind=kind,
                    config=wid_conf,
                    value=tk.StringVar(
                        value=cur_value,
                        name=f'itemconf_{data.id}_{wid_id}',
                    ),
                ))
        # If we are new, write our defaults to config.
        CONFIG.save_check()

        return cls(
            data.id,
            group_name,
            desc,
            widgets,
            multi_widgets,
        )

    def iter_trans_tokens(self) -> Iterator[TransTokenSource]:
        """Yield translation tokens for this config group."""
        source = f'configgroup/{self.id}'
        yield self.name, source + '.name'
        for widget in itertools.chain(self.widgets, self.multi_widgets):
            yield widget.name, f'{source}/{widget.id}.name'
            yield widget.tooltip, f'{source}/{widget.id}.tooltip'

    def add_over(self, override: 'ConfigGroup') -> None:
        """Override a ConfigGroup to add additional widgets."""
        # Make sure they don't double-up.
        conficts = self.widget_ids() & override.widget_ids()
        if conficts:
            raise ValueError('Duplicate IDs in "{}" override - {}', self.id, conficts)

        self.widgets.extend(override.widgets)
        self.multi_widgets.extend(override.multi_widgets)
        self.desc = tkMarkdown.join(self.desc, override.desc)

    def widget_ids(self) -> Set[str]:
        """Return the set of widget IDs used."""
        widgets: List[Iterable[Widget]] = [self.widgets, self.multi_widgets]
        return {wid.id for wid_list in widgets for wid in wid_list}

    @staticmethod
    def export(exp_data: packages.ExportData) -> None:
        """Write all our values to the config."""
        for conf in exp_data.packset.all_obj(ConfigGroup):
            config_section = CONFIG[conf.id]
            for s_wid in conf.widgets:
                if s_wid.has_values:
                    config_section[s_wid.id] = s_wid.value.get()
            for m_wid in conf.multi_widgets:
                for num, var in m_wid.values:
                    config_section[f'{m_wid.id}_{num}'] = var.get()
            if not config_section:
                del CONFIG[conf.id]
        CONFIG.save_check()

    async def create_widgets(self, master: ttk.Frame) -> None:
        """Create the widgets for this config."""
        frame = ttk.Frame(master)
        frame.columnconfigure(0, weight=1)
        row = 0

        widget_count = len(self.widgets) + len(self.multi_widgets)
        wid_frame: tk.Widget
        widget: tk.Widget

        # Now make the widgets.
        if self.widgets:
            for row, s_wid in enumerate(self.widgets):
                wid_frame = ttk.Frame(frame)
                wid_frame.grid(row=row, column=0, sticky='ew')
                wid_frame.columnconfigure(1, weight=1)
                await trio.sleep(0)

                label: Optional[ttk.Label] = None
                if s_wid.name:
                    if s_wid.kind.is_wide:
                        wid_frame = localisation.set_text(
                            ttk.LabelFrame(wid_frame),
                            TRANS_COLON.format(text=s_wid.name),
                        )
                        wid_frame.grid(row=0, column=0, columnspan=2, sticky='ew', pady=5)
                        wid_frame.columnconfigure(0, weight=1)
                    else:
                        label = ttk.Label(wid_frame)
                        localisation.set_text(label, TRANS_COLON.format(text=s_wid.name))
                        label.grid(row=0, column=0)
                create_func = _UI_IMPL_SINGLE[s_wid.kind]
                try:
                    with logger.context(f'{self.id}:{s_wid.id}'):
                        widget, s_wid.ui_cback = await create_func(wid_frame, s_wid.value, s_wid.config)
                except Exception:
                    LOGGER.exception('Could not construct widget {}.{}', self.id, s_wid.id)
                    continue

                if label is not None:
                    widget.grid(row=0, column=1, sticky='e')
                else:
                    widget.grid(row=0, column=0, columnspan=2, sticky='ew')
                if s_wid.has_values:
                    await config.APP.set_and_run_ui_callback(
                        WidgetConfig, s_wid.apply_conf, f'{s_wid.group_id}:{s_wid.id}',
                    )
                if s_wid.tooltip:
                    add_tooltip(widget, s_wid.tooltip)
                    if label is not None:
                        add_tooltip(label, s_wid.tooltip)
                    add_tooltip(wid_frame, s_wid.tooltip)

        if self.widgets and self.multi_widgets:
            ttk.Separator(orient='horizontal').grid(row=1, column=0, sticky='ew')

        # Continue from wherever we were.
        for row, m_wid in enumerate(self.multi_widgets, start=row + 1):
            # If we only have 1 widget, don't add a redundant title.
            if widget_count == 1 or not m_wid.name:
                wid_frame = ttk.Frame(frame)
            else:
                wid_frame = localisation.set_text(
                    ttk.LabelFrame(frame),
                    TRANS_COLON.format(text=m_wid.name),
                )

            try:
                multi_func = _UI_IMPL_MULTI[m_wid.kind]
            except KeyError:
                multi_func = widget_timer_generic(_UI_IMPL_SINGLE[m_wid.kind])

            wid_frame.grid(row=row, column=0, sticky='ew', pady=5)
            try:
                with logger.context(f'{self.id}:{m_wid.id}'):
                    async for tim_val, value in multi_func(
                        wid_frame,
                        m_wid.values,
                        m_wid.config,
                    ):
                        m_wid.ui_cbacks[tim_val] = value
            except Exception:
                LOGGER.exception('Could not construct widget {}.{}', self.id, m_wid.id)
                continue
            await config.APP.set_and_run_ui_callback(
                WidgetConfig, m_wid.apply_conf, f'{m_wid.group_id}:{m_wid.id}',
            )
            await trio.sleep(0)

            if m_wid.tooltip:
                add_tooltip(wid_frame, m_wid.tooltip)
        self.ui_frame = frame


# Special group injected for the stylevar display.
STYLEVAR_GROUP = ConfigGroup('_STYLEVAR', TransToken.ui('Style Properties'), '', [], [])


async def make_pane(tool_frame: tk.Frame, menu_bar: tk.Menu, update_item_vis: Callable[[], None]) -> None:
    """Create the item properties pane, with the widgets it uses.

    update_item_vis is passed through to the stylevar pane.
    """
    global window

    window = SubPane(
        TK_ROOT,
        title=TransToken.ui('Style/Item Properties'),
        name='item',
        legacy_name='style',
        menu_bar=menu_bar,
        resize_y=True,
        tool_frame=tool_frame,
        tool_img='icons/win_itemvar',
        tool_col=3,
    )

    ordered_conf: List[ConfigGroup] = sorted(
        packages.LOADED.all_obj(ConfigGroup),
        key=lambda grp: str(grp.name),
    )
    ordered_conf.insert(0, STYLEVAR_GROUP)

    selection_frame = ttk.Frame(window)
    selection_frame.grid(row=0, column=0, columnspan=2, sticky='ew')

    arrow_left = ttk.Button(
        selection_frame,
        text='◀', width=2,
        command=lambda: select_directional(-1),
    )
    group_label = ttk.Label(
        selection_frame,
        text='Group Name', anchor='center',
        cursor=tk_tools.Cursors.LINK,
    )
    arrow_right = ttk.Button(
        selection_frame,
        text='▶', width=2,
        command=lambda: select_directional(+1),
    )

    arrow_left.grid(row=0, column=0)
    group_label.grid(row=0, column=1, sticky='ew')
    selection_frame.columnconfigure(1, weight=1)
    arrow_right.grid(row=0, column=2)

    label_font = tk.font.nametofont('TkHeadingFont').copy()
    label_font.config(weight='bold')
    group_label['font'] = label_font

    group_menu = tk.Menu(group_label, tearoff=False)
    group_var = tk.StringVar(window)

    ttk.Separator(window, orient='horizontal').grid(row=1, column=0, columnspan=2, sticky='EW')

    # Need to use a canvas to allow scrolling.
    canvas = tk.Canvas(window, highlightthickness=0)
    canvas.grid(row=2, column=0, sticky='NSEW', padx=(5, 0))
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    scrollbar = ttk.Scrollbar(
        window,
        orient='vertical',
        command=canvas.yview,
    )
    scrollbar.grid(row=2, column=1, sticky="ns")
    canvas['yscrollcommand'] = scrollbar.set

    tk_tools.add_mousewheel(canvas, canvas, window)
    canvas_frame = ttk.Frame(canvas)
    frame_winid = canvas.create_window(0, 0, window=canvas_frame, anchor="nw")
    canvas_frame.columnconfigure(0, weight=1)
    canvas_frame.rowconfigure(1, weight=1)

    stylevar_frame = ttk.Frame(canvas_frame)
    await StyleVarPane.make_stylevar_pane(stylevar_frame, packages.LOADED, update_item_vis)

    loading_text = ttk.Label(canvas_frame)
    localisation.set_text(loading_text, TransToken.ui('Loading...'))
    loading_text.grid(row=0, column=0, sticky='ew')
    loading_text.grid_forget()

    STYLEVAR_GROUP.ui_frame = stylevar_frame
    cur_group = STYLEVAR_GROUP
    win_max_width = 0

    async def display_group(group: ConfigGroup) -> None:
        """Callback to display the group in the UI, once constructed."""
        nonlocal win_max_width
        if cur_group is group:
            if loading_text.winfo_ismapped():
                loading_text.grid_forget()
            group.ui_frame.grid(row=1, column=0, sticky='ew')
            await tk_tools.wait_eventloop()
            width = group.ui_frame.winfo_reqwidth()
            canvas['scrollregion'] = (
                0, 0,
                width,
                group.ui_frame.winfo_reqheight()
            )
            if width > win_max_width:
                canvas['width'] = width
                win_max_width = width
                scroll_width = scrollbar.winfo_width() + 10
                window.geometry(f'{width + scroll_width}x{window.winfo_height()}')
            canvas.itemconfigure(frame_winid, width=win_max_width)

    def select_group(group: ConfigGroup) -> None:
        """Callback when the combobox is changed."""
        nonlocal cur_group
        new_group = group
        if new_group is cur_group:  # Pointless to reselect.
            return
        if cur_group.ui_frame is not None:
            cur_group.ui_frame.grid_forget()
        cur_group = new_group
        update_disp()
        if new_group.ui_frame is not None:
            # Ready, add.
            background_run(display_group, new_group)
        else:  # Begin creating, or loading.
            loading_text.grid(row=0, column=0, sticky='ew')
            if not new_group.creating:
                async def task() -> None:
                    """Create the widgets, then display."""
                    await new_group.create_widgets(canvas_frame)
                    await display_group(new_group)

                background_run(task)
                new_group.creating = True

    def select_directional(direction: int) -> None:
        """Change the selection in some direction."""
        # Clamp to ±1 since scrolling can send larger numbers.
        pos = ordered_conf.index(cur_group) + (+1 if direction > 0 else -1)
        if 0 <= pos < len(ordered_conf):
            select_group(ordered_conf[pos])

    def update_disp() -> None:
        """Update widgets if the group has changed."""
        localisation.set_text(group_label, TRANS_GROUP_HEADER.format(
            name=cur_group.name,
            page=ordered_conf.index(cur_group) + 1,
            count=len(ordered_conf),
        ))
        pos = ordered_conf.index(cur_group)
        group_var.set(cur_group.id)
        arrow_left.state(['disabled' if pos == 0 else '!disabled'])
        arrow_right.state(['disabled' if pos + 1 == len(ordered_conf) else '!disabled'])

    @localisation.add_callback(call=True)
    def update_selector() -> None:
        """Update translations in the display, reordering if necessary."""
        # Stylevar always goes at the start.
        ordered_conf.sort(key=lambda grp: (0 if grp is STYLEVAR_GROUP else 1, str(grp.name)))
        # Remake all the menu widgets.
        group_menu.delete(0, 'end')
        for group in ordered_conf:
            group_menu.insert_radiobutton(
                'end', label=str(group.name),
                variable=group_var, value=group.id,
                command=functools.partial(select_group, group),
            )
        update_disp()

    tk_tools.bind_leftclick(group_label, lambda evt: group_menu.post(evt.x_root, evt.y_root))
    tk_tools.bind_mousewheel([
        selection_frame, arrow_left, arrow_right, group_label,
    ], select_directional)
    group_label.bind('<Enter>', lambda e: group_label.configure(foreground='#2873FF'))
    group_label.bind('<Leave>', lambda e: group_label.configure(foreground=''))

    await tk_tools.wait_eventloop()

    def canvas_reflow(_) -> None:
        """Update canvas when the window resizes."""
        canvas['scrollregion'] = canvas.bbox('all')

    canvas.bind('<Configure>', canvas_reflow)
    await display_group(cur_group)


def widget_timer_generic(widget_func: SingleCreateFunc[ConfT]) -> MultiCreateFunc[ConfT]:
    """For widgets without a multi version, do it generically."""
    async def generic_func(
        parent: tk.Widget,
        values: List[Tuple[str, tk.StringVar]],
        conf: ConfT,
    ) -> AsyncIterator[Tuple[str, UpdateFunc]]:
        """Generically make a set of labels."""
        for row, (tim_val, var) in enumerate(values):
            timer_disp = TIMER_NUM_TRANS[tim_val]
            parent.columnconfigure(1, weight=1)

            label = ttk.Label(parent)
            localisation.set_text(label, TRANS_COLON.format(text=timer_disp))
            label.grid(row=row, column=0)
            widget, update = await widget_func(parent, var, conf)
            yield tim_val, update
            widget.grid(row=row, column=1, sticky='ew')

    return generic_func


def multi_grid(
    values: List[Tuple[str, tk.StringVar]],
    columns: int = 10,
) -> Iterator[Tuple[int, int, str, TransToken, tk.StringVar]]:
    """Generate the row and columns needed for a nice layout of widgets."""
    for tim, var in values:
        if tim == 'inf':
            tim_disp = INF
            index = 0
        else:
            tim_disp = TIMER_NUM_TRANS[tim]
            index = int(tim)
        row, column = divmod(index - 1, columns)
        yield row, column, tim, tim_disp, var


def widget_sfx(*args) -> None:
    """Play sounds when interacting."""
    sound.fx_blockable('config')


@register('itemvariant', 'variant')
@attrs.frozen
class ItemVariantConf:
    """Configuration for the special widget."""
    item_id: str

    @classmethod
    def parse(cls, conf: Keyvalues) -> Self:
        """Parse from configs."""
        return cls(conf['ItemID'])


@ui_single_wconf(ItemVariantConf)
async def widget_item_variant(parent: tk.Widget, var: tk.StringVar, conf: ItemVariantConf) -> Tuple[tk.Widget, UpdateFunc]:
    """Special widget - chooses item variants.

    This replicates the box on the right-click menu for items.
    It's special-cased in the above code.
    """
    from app import contextWin
    # We don't use the TK variable passed to us.

    try:
        item = UI.item_list[conf.item_id]
    except KeyError:
        raise ValueError('Unknown item "{}"!'.format(conf.item_id))

    if item.id == 'ITEM_BEE2_SIGNAGE':
        # Even more special case, display the "configure signage" button.
        return await signage_ui.init_widgets(parent), nop_update

    version_lookup: Optional[List[str]] = None

    def update_data() -> None:
        """Refresh the data in the list."""
        nonlocal version_lookup
        version_lookup = contextWin.set_version_combobox(combobox, item)

    def change_callback(e: tk.Event=None):
        """Change the item version."""
        item.change_version(version_lookup[combobox.current()])

    combobox = ttk.Combobox(
        parent,
        exportselection=False,
        values=[''],
    )
    combobox.state(['readonly'])  # Prevent directly typing in values
    combobox.bind('<<ComboboxSelected>>', change_callback)

    ITEM_VARIANT_LOAD.append((item.id, update_data))
    update_data()
    return combobox, nop_update


KIND_ITEM_VARIANT = WIDGET_KINDS['itemvariant']


# Load all the widgets.
from . import checkmark, color, dropdown, slider, string, timer
