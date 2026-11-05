# SPDX-License-Identifier: Apache-2.0
"""Shared controls for consistent desktop layouts."""
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QPainter, QPen, QPalette
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QSizePolicy


class ComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('appCombo')
        self.setFixedHeight(38)
        self.setMinimumContentsLength(10)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.currentTextChanged.connect(self.setToolTip)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group=QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(QPen(self.palette().color(group,QPalette.ColorRole.Text),1.5,
            Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        x,y=self.width()-17,self.height()/2
        painter.drawLine(QPointF(x-4,y-2),QPointF(x,y+2))
        painter.drawLine(QPointF(x,y+2),QPointF(x+4,y-2))



class ElidedLabel(QLabel):
    def __init__(self,parent=None):
        super().__init__(parent);self._full_text=''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)

    def setText(self,text):
        self._full_text=text;self.setToolTip(text);self.setAccessibleName(text);self.update_text()

    def update_text(self):
        super().setText(self.fontMetrics().elidedText(self._full_text,Qt.TextElideMode.ElideRight,max(0,self.contentsRect().width())))

    def resizeEvent(self,event):
        super().resizeEvent(event);self.update_text()


def field(label, control):
    widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
    label.setObjectName('fieldLabel');label.setBuddy(control)
    layout.addWidget(label);layout.addWidget(control)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    return widget


class EmptyState(QWidget):
    """Non-interactive guidance inside an empty view, without hiding its headers."""
    def __init__(self, view, is_empty=None):
        super().__init__(view.viewport())
        self.view=view;self.is_empty=is_empty or (lambda:view.model().rowCount()==0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,12,20,12);layout.setSpacing(6)
        layout.addStretch()
        self.title=QLabel();self.title.setObjectName('emptyTitle');self.title.setAlignment(Qt.AlignmentFlag.AlignCenter);self.title.setWordWrap(True)
        self.detail=QLabel();self.detail.setObjectName('emptyDetail');self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter);self.detail.setWordWrap(True)
        layout.addWidget(self.title);layout.addWidget(self.detail);layout.addStretch()
        view.viewport().installEventFilter(self)
        if hasattr(view,'model'):
            for signal in (view.model().rowsInserted,view.model().rowsRemoved,view.model().modelReset):signal.connect(self.refresh)
        self.refresh()

    def set_text(self,title,detail=''):
        self.title.setText(title);self.detail.setText(detail);self.detail.setVisible(bool(detail));self.refresh()

    def refresh(self,*args):
        self.setGeometry(self.view.viewport().rect());self.setVisible(self.is_empty());self.raise_()

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.refresh()
        return False
