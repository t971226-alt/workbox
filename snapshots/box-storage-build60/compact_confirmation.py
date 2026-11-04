"""Small confirmation surface over the existing transactional intake engine."""
from collections import Counter
from pathlib import Path
from PySide6.QtCore import Qt,QObject,QEvent,QTimer
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QSizePolicy
from workspace_chrome import chrome_button,colors
from file_families import family_for,infer,compatible


def contains(profile):
    names={'word':'Word','pdf':'PDF','text':'TXT','archive':'压缩包','sheet':'Excel',
           'slides':'PPT','video':'视频','image':'图片','audio':'音频'}
    count=Counter(names.get(profile.get('family_map',{}).get(k) or family_for(Path(k).suffix),Path(k).suffix.lstrip('.').upper() or '文件') for k in profile['kinds'])
    return '、'.join(f'{name} ×{n}' for name,n in count.items())


class Confirmation(QObject):
    def __init__(self,dialog):
        super().__init__(dialog)
        self.dialog=dialog;self.view=dialog.form.grouped_view;self.engine=dialog.form
        self.fit_timer=QTimer(self);self.fit_timer.setSingleShot(True);self.fit_timer.timeout.connect(dialog.fit_content)
        for w in (dialog,dialog.extract_body,self.engine,self.view):w.installEventFilter(self)
        self.routes={};self.view.compact_confirmation=self
        self.view.folders.hide()
        for g in self.view.groups:
            # Retain hidden engine controls for matching, cancellation and copies.
            g['expand'].setCheckable(False);g['expand'].setToolTip('软件名称')
            g['expand'].setProperty('leftAligned',True)
            g['options'].hide();g['detail'].hide()
            change=g['card'].layout().itemAt(1).layout().itemAt(1).widget()
            change.toggled.disconnect()
            change.setChecked(False);g['configure']=change
            g['compact_options']=QWidget();layout=QVBoxLayout(g['compact_options']);layout.setContentsMargins(0,0,0,0);layout.setSpacing(5)
            row=QHBoxLayout();row.addWidget(QLabel('软件名称'));row.addWidget(g['software'],1);layout.addLayout(row)
            if g.get('saved'):
                g['software'].setReadOnly(True);g['software'].setToolTip('已有任务保留原名称；修改名称请使用任务右键菜单。')
            g['software'].editingFinished.connect(lambda g=g:self.rename_folder(g))
            g['card'].layout().addWidget(g['compact_options']);g['compact_options'].hide()
            change.toggled.connect(g['compact_options'].setVisible);change.toggled.connect(self.view.fit_later)
            g['compact_path']=QLabel();g['compact_path'].setWordWrap(True);g['compact_path'].setTextFormat(Qt.PlainText);g['card'].layout().addWidget(g['compact_path'])
            g['compact_path'].setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            g['compact_notice']=QLabel();g['compact_notice'].setWordWrap(True);g['card'].layout().addWidget(g['compact_notice'])
            g['unmatched_files']=QLabel();g['unmatched_files'].setWordWrap(True);g['unmatched_files'].setTextFormat(Qt.PlainText)
            g['card'].layout().insertWidget(g['card'].layout().indexOf(g['compact_notice']),g['unmatched_files'])
            from unmatched_intake import ACTION
            g['new_rule_action']=chrome_button(ACTION,variant='primary')
            g['new_rule_action'].clicked.connect(lambda checked=False,g=g:self.new_rule(g))
            g['card'].layout().addWidget(g['new_rule_action'],alignment=Qt.AlignRight)
            g['rule_label'].setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            g['expand'].setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            g['expand'].setMinimumWidth(0)
        from theme import theme_signal
        theme_signal().connect(self.apply_theme);self.apply_theme();self.refresh()

    def rename_folder(self,g):
        if not g.get('saved'):g['route'].software.setText(g['software'].text().strip())
        self.view.schedule()

    def apply_theme(self,*_):
        c=colors();v=self.view;v.setObjectName('compactConfirmation');v.body.setObjectName('confirmationBody')
        for w in (self.engine,v,v.body,v.scroll.viewport()):
            palette=w.palette();palette.setColor(QPalette.Window,QColor(c['surface']));palette.setColor(QPalette.Base,QColor(c['surface']));palette.setColor(QPalette.WindowText,QColor(c['ink']));w.setPalette(palette);w.setAutoFillBackground(True);w.setAttribute(Qt.WA_StyledBackground,True)
        v.setStyleSheet('QWidget#compactConfirmation,QWidget#confirmationBody{background:'+c['surface']+';} QFrame#intakeSoftwareCard{border:1px solid '+c['border']+';border-radius:6px;background:'+c['surface']+';}')
        v.scroll.setStyleSheet('QScrollArea{border:none;background:'+c['surface']+';}');v.scroll.viewport().setStyleSheet('background:'+c['surface']+';')
        for label in (v.summary,v.all):label.setStyleSheet('background:'+c['surface']+';color:'+c['ink']+';font-size:12px;')
        for g in v.groups:
            for key in ('check','status','rule_label','compact_path','compact_notice','unmatched_files'):
                g[key].setStyleSheet('color:'+c['ink']+';font-size:12px;')
            g['software'].setStyleSheet('color:'+c['ink']+';background:'+c['field']+';border:1px solid '+c['border']+';border-radius:5px;padding:5px;')
            for label in g['compact_options'].findChildren(QLabel):label.setStyleSheet('color:'+c['ink']+';font-size:12px;')

    def eventFilter(self,obj,event):
        if event.type()==QEvent.Hide and obj is self.dialog:self.fit_timer.stop()
        if event.type()==QEvent.LayoutRequest and self.dialog.isVisible():self.fit_timer.start(0)
        return False

    def saved_profiles(self):return self.engine.service.classifications.profiles(True,include_archives=True)

    def coverage(self,g):
        entries=[self.engine.proposal['entries'][i] for i in g['indices'] if self.view.checked(i)]
        existing=next((e.get('existing') for e in entries if e.get('existing')),None)
        if existing:
            with self.engine.service.db.connect() as c:
                return {e['path']:self.engine.service.production.match_kind(c,existing['id'],Path(e['path']).name) for e in entries}
        from unmatched_intake import mapping
        return mapping(self.view.profile(g),entries,g['software'].text())

    def refresh(self):
        saved={p['name'] for p in self.saved_profiles()}
        for g in self.view.groups:
            p=self.view.profile(g);existing=any(self.engine.existing_row(i) for i in g['indices'])
            extras={k for i in g['indices'] for k in self.engine.proposal['entries'][i].get('extra_kinds',[])} if existing else set()
            display=dict(p,kinds=[k for k in p['kinds'] if k not in extras])
            valid=existing or p['name'] in saved
            g['expand'].setText(g['software'].text());g['expand'].setChecked(False)
            g['detail'].hide();g['options'].hide()
            g['rule_label'].setText(('收纳规则：'+p['name']+'\n包含：'+contains(display)) if valid else '收纳规则：未匹配\n请点击下方“新建规则”，保存后自动匹配。')
            route=g['route']
            previous=self.routes.get(id(g))
            if previous is None or previous[0] is not route:
                if previous:
                    previous[1].hide();previous[1].deleteLater()
                self.routes[id(g)]=(route,route.body)
                route.toggle.hide();route.summary.hide();route.hint.hide();route.tree.hide();route.names.hide();route.names_toggle.hide()
                route.body.setParent(g['compact_options']);g['compact_options'].layout().insertWidget(0,route.body);route.body.show()
                # One folder selector, one software name. Keep the hidden model.
                route.root.hide();route.directory.hide();route.browse_button.hide()
                for child in route.body.findChildren(QLabel):child.hide()
                route.choice.setAccessibleName('保存文件夹');route.choice.show()
            g['compact_path'].setText(route.summary.text().replace('保存文件夹：','保存到：'))
            selected=[i for i in g['indices'] if self.view.checked(i)]
            unmatched=[i for i in selected if not self.engine.chosen_kind(i)]
            coverage=self.coverage(g)
            needs_rule=bool(selected) and (not valid or bool(unmatched) or not all(coverage.values()))
            g['needs_rule']=needs_rule;g['new_rule_action'].setVisible(needs_rule)
            names=[Path(self.engine.proposal['entries'][i]['path']).name for i in selected if not valid or not coverage.get(self.engine.proposal['entries'][i]['path']) or i in unmatched]
            g['unmatched_files'].setText('未匹配：'+'、'.join(names[:3])+(' 等 '+str(len(names))+' 份' if len(names)>3 else ''))
            g['unmatched_files'].setToolTip('\n'.join(names));g['unmatched_files'].setVisible(needs_rule and bool(names))
            g['compact_path'].setVisible(not needs_rule)
            if needs_rule:
                g['status'].setText(str(len({k for k in coverage.values() if k}))+' / '+str(len(display['kinds']))+' 份')
                g['rule_label'].setText(('匹配到：'+p['name']+' · 未完全匹配\n包含：'+contains(display)) if valid else '收纳规则：未匹配')
            choices=[ids for ids in getattr(self.engine,'_keyword_choice_groups',[]) if ids[0] in g['indices'] and not any(self.view.checked(i) for i in ids)]
            from unmatched_intake import MESSAGE
            notice=(MESSAGE if needs_rule else
                    '有多份同项文件，请先选择匹配文件。' if choices else
                    '请选择保存文件夹。' if route.unresolved else '')
            g['compact_notice'].setText(notice);g['compact_notice'].setVisible(bool(notice))
        if hasattr(self.dialog,'match_button'):
            self.dialog.match_button.setVisible(bool(getattr(self.engine,'_keyword_choice_groups',[])))
        self.dialog.save_button.setEnabled(self.dialog.raw_radio.isChecked() or not any(g.get('needs_rule') for g in self.view.groups))

    def validate(self):
        saved={p['name'] for p in self.saved_profiles()}
        for g in self.view.groups:
            if not any(self.view.checked(i) for i in g['indices']):continue
            if g.get('needs_rule'):
                from unmatched_intake import MESSAGE
                raise ValueError(MESSAGE+'文件尚未保存。')
            if self.view.profile(g).get('intake_mode')=='original':
                raise ValueError('这条压缩包规则用于原包保存，请切换“原包直接保存”，或取消后重新拖入压缩包。')
            if not any(self.engine.existing_row(i) for i in g['indices']) and self.view.profile(g)['name'] not in saved:
                raise ValueError('请先点击“新建规则”，保存规则后再收录。')
            if any(self.view.checked(i) and not self.engine.chosen_kind(i) for i in g['indices']):
                raise ValueError('有文件未匹配，请在规则管理中完善类型或关键词；文件尚未保存。')
            panel=g.get('onboarding')
            if panel:
                # Every selected file is now covered by a persisted rule. No
                # hidden keep/skip decision may discard an input implicitly.
                panel.confirmed=True

    def rematch(self,profile,target=None):
        self.dialog.error.clear();self.dialog.error.hide()
        if profile.get('intake_mode')=='original':
            if hasattr(self.dialog,'raw_controls'):
                from copy import deepcopy
                key='confirmed-archive-rule'
                self.dialog.raw_folders.items[key]=dict(id=key,name='',rule=deepcopy(profile),_direct=True)
                self.dialog.raw_destination=key
                for item in self.dialog.raw_controls:item['route'].set_profile(profile)
                self.dialog.raw_radio.setChecked(True)
            else:self.dialog.fail('压缩包规则已保存；请取消后重新拖入压缩包，使用原包保存。')
            return
        self.view.register(profile)
        saved={p['name'] for p in self.saved_profiles()}
        for g in self.view.groups:
            if target is not None and g is not target:continue
            if any(self.engine.existing_row(i) for i in g['indices']):
                if target is None:continue
                # The saved task remains pinned to its original snapshot and
                # files. This new rule explicitly creates an independent record.
                for i in g['indices']:
                    e=self.engine.proposal['entries'][i]
                    if e.pop('existing',None):e['independent_new_rule']=True
                    for key in ('existing_families','existing_patterns','existing_base_kinds','extra_kinds','extra_families','extra_patterns'):e.pop(key,None)
                    self.engine.class_controls[i].setEnabled(True)
                g['saved']=None;g['location_dirty']=False;g['custom_bases'].clear();g['folder_name'].setText(g['software'].text())
            kinds=[infer(Path(self.engine.proposal['entries'][i]['path']).name,profile['kinds'],profile.get('family_map'),profile.get('aliases'),g['software'].text(),profile.get('name_patterns')) for i in g['indices']]
            for pos,i in enumerate(g['indices']):
                if not kinds[pos]:
                    kinds[pos]=next((k for k in profile['kinds'] if profile.get('name_patterns',{}).get(k,{}).get('mode')=='type' and compatible(k,self.engine.proposal['entries'][i]['path'],profile.get('family_map'))),None)
            if not any(kinds):continue
            current=self.view.profile(g)
            if target is None and current['name'] in saved and current['name']!=profile['name'] and sum(bool(k) for k in kinds)<=sum(bool(self.engine.chosen_kind(i)) for i in g['indices']):continue
            self.view.assign(g,profile)
            for i,kind in zip(g['indices'],kinds):
                self.engine.kind_controls[i].setCurrentIndex(max(0,self.engine.kind_controls[i].findData(kind)))
            self.view.allocate(g,profile)
            panel=g.get('onboarding')
            if panel:panel.confirmed=all(self.engine.chosen_kind(i) for i in g['indices'])
        self.engine.choose_default_versions();self.view.refresh()

    def new_rule(self,target=None):
        from management_ui import RuleManagementDialog
        target=target or next((g for g in self.view.groups if g.get('needs_rule')),self.view.groups[0] if self.view.groups else None)
        if target is None:return
        entries=[dict(self.engine.proposal['entries'][i],name=target['software'].text()) for i in target['indices'] if self.view.checked(i)]
        if not entries:self.dialog.fail('请先勾选本次要收录的软件。');return
        from unmatched_intake import draft
        context=dict(draft=draft(self.engine.service,entries),entries=entries,software=target['software'].text(),existing=any(e.get('existing') for e in entries))
        d=RuleManagementDialog(self.engine.service,self.dialog,new=True,intake_context=context)
        d.saved.connect(lambda p:self.rematch(p,target));d.saved.connect(lambda _:d.accept())
        try:d.exec()
        finally:d.deleteLater()
        self.view.schedule();self.view.fit_later()


def install(dialog,actions):
    dialog._intake_width=500
    dialog.note.setText('核对软件、收纳规则和保存位置')
    dialog.compact_confirmation=Confirmation(dialog)
    dialog.raw_radio.toggled.connect(dialog.compact_confirmation.refresh)
    dialog.new_rule_button=chrome_button('新建规则',variant='primary')
    actions.insertWidget(actions.indexOf(dialog.save_button),dialog.new_rule_button)
    dialog.new_rule_button.clicked.connect(dialog.compact_confirmation.new_rule)
    dialog.new_rule_button.hide()  # Each unresolved software has one contextual entry.
    dialog.match_button=chrome_button('选择匹配文件')
    dialog.layout().insertWidget(dialog.layout().count()-1,dialog.match_button,alignment=Qt.AlignRight)
    dialog.match_button.clicked.connect(dialog.form.versions_button.click)
    dialog.match_button.hide()
    dialog.cancel_button.setProperty('variant','primary')
    # ChromeButton stores the variant as an attribute, not a Qt property.
    dialog.cancel_button.variant=dialog.save_button.variant
    dialog.setMinimumWidth(0);dialog.resize(500,240)
    dialog.compact_confirmation.refresh()
