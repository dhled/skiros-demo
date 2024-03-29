import os
import rospy
import rospkg

from functools import partial

from python_qt_binding import loadUi
from python_qt_binding.QtCore import Qt, QTimer, Slot, pyqtSignal
import python_qt_binding.QtCore as QtCore
import python_qt_binding.QtGui as QtGui
from python_qt_binding.QtWidgets import QLabel, QTableWidgetItem, QTreeWidgetItem, QWidget, QCheckBox, QComboBox, QLineEdit, QDialog, QSizePolicy, QShortcut

import skiros2_common.tools.logger as log
import skiros2_common.core.utils as utils
import skiros2_common.ros.utils as rosutils
from skiros2_common.core.params import ParamTypes
from skiros2_common.core.world_element import Element
from skiros2_common.core.property import Property
import skiros2_msgs.msg as msgs
import skiros2_world_model.ros.world_model_interface as wmi
import skiros2_skill.ros.skill_layer_interface as sli
from skiros2_common.core.abstract_skill import State
from copy import deepcopy
from std_msgs.msg import String

from interactive_markers.interactive_marker_server import InteractiveMarkerServer
from visualization_msgs.msg import InteractiveMarker, InteractiveMarkerControl, Marker, InteractiveMarkerFeedback
from numpy.linalg import norm
from threading import Lock
from datetime import datetime


class SkirosAddObjectDialog(QDialog):
    #==============================================================================
    #  Modal dialog
    #==============================================================================

    default_type = 'sumo:Object'

    def __init__(self, *args, **kwargs):
        """Implements a dialog to create a new object for the world model.

        Implementation of the modal dialog to select object types from the available ontology/world model.
        Allows filtering of the objects by (sub)type.
        The dialog saves the selection in the 'object' property.

        Args:
            *args: Description
            **kwargs: Description
        """
        super(SkirosAddObjectDialog, self).__init__(*args, **kwargs)
        self.setObjectName('SkirosAddObjectDialog')
        ui_file = os.path.join(rospkg.RosPack().get_path('skiros2_gui'), 'src/skiros2_gui/core', 'skiros_gui_add_object_dialog.ui')
        loadUi(ui_file, self)

        self._comboBoxes = []
        self.create_comboBox(label='Type')
        self.comboBox_individual.clear()
        [self.comboBox_individual.addItem(l, d) for l, d in self.get_individuals(self.default_type).iteritems()]

    @property
    def object(self):
        """Access to the currently selected object type.

        Returns:
            str: Selected (ontology) type (e.g. skiros:Product)
        """
        return self.comboBox_individual.itemData(self.comboBox_individual.currentIndex())

    def on_select_type(self, id, index):
        """Callback for change selection in dropdown lists.

        Adds and removes dropdown list to/from the dialog that are used to filter subtypes based on the current selection.

        Args:
            id (int): Number of the combobox that dispatched the callback
            index (int): Number of the selected item in the current combobox (id)
        """
        # log.debug(self.__class__.__name__, 'Selected {}: {}'.format(id, index))

        # clear filters after selected
        while id < len(self._comboBoxes) - 1:
            # log.debug(self.__class__.__name__, 'Delete {}'.format(id+1))
            label = self.formLayout.labelForField(self._comboBoxes[id + 1])
            label.deleteLater()
            self._comboBoxes[id + 1].deleteLater()
            del self._comboBoxes[id + 1]

        # get current selection
        if index > 0:  # if not 'All' is selected
            selected = self._comboBoxes[id].itemData(self._comboBoxes[id].currentIndex())
        elif id > 0:  # if 'All' is selected and it is not the first combo box
            selected = self._comboBoxes[id - 1].itemData(self._comboBoxes[id - 1].currentIndex())
        else:  # if 'All' is selected and it is the first combo box
            selected = self.default_type
        # log.debug(self.__class__.__name__, 'Selected type {}'.format(selected))

        # create new combo box if not 'All' is selected
        if index > 0:
            self.create_comboBox(selected)
            # log.debug(self.__class__.__name__, 'Created {}'.format(len(self._comboBoxes)-1))

        # update list of individuals
        self.comboBox_individual.clear()
        if index > 0 or (id > 0 and index == 0):
            self.comboBox_individual.addItem('new ' + utils.ontology_type2name(selected), selected)
        [self.comboBox_individual.addItem(l, d) for l, d in self.get_individuals(selected).iteritems()]
        QTimer.singleShot(0, self.adjustSize)

    def create_comboBox(self, subtype='sumo:Object', label='Subtype'):
        """Inserts a new combobox in the dialog based on the subtype.

        Helper function that creates a combobox and fills the list with filtered items from the ontology/world model.

        Args:
            subtype (str, optional): Type to be used to retrieve world model items for the dropdown list
            label (str, optional): Label for the dropdown list
        """
        comboBox = QComboBox()
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        comboBox.setSizePolicy(sizePolicy)
        comboBox.addItem('All')
        [comboBox.addItem(l, d) for l, d in self.get_types(subtype).iteritems()]
        comboBox.currentIndexChanged.connect(partial(self.on_select_type, len(self._comboBoxes)))
        self.formLayout.insertRow(len(self._comboBoxes), label, comboBox)
        self._comboBoxes.append(comboBox)

    def get_types(self, subtype):
        """Retrieves available subtype from the ontology.

        Args:
            subtype (str): Filter for object types

        Returns:
            dict(str, str): Keys: Short type name. Values: Type identifier (e.g. {'Product': 'skiros:Product'})
        """
        return utils.ontology_type2name_dict(self.parent()._wmi.get_sub_classes(subtype, False))

    def get_individuals(self, subtype):
        """Retrieves available individuals from the world model.

        Args:
            subtype (str): Filter for object types

        Returns:
            dict(str, str): Keys: Short type name. Values: Type identifier (e.g. {'starter': 'skiros:starter'})
        """
        return utils.ontology_type2name_dict(self.parent()._wmi.get_individuals(subtype, True))


class SkirosInteractiveMarkers:
    default_box_size = 0.1

    def on_marker_feedback(self, feedback):
        s = "Feedback from marker '" + feedback.marker_name
        s += "' / control '" + feedback.control_name + "'"

        mp = ""
        if feedback.mouse_point_valid:
            mp = " at " + str(feedback.mouse_point.x)
            mp += ", " + str(feedback.mouse_point.y)
            mp += ", " + str(feedback.mouse_point.z)
            mp += " in frame " + feedback.header.frame_id
        print s
        print mp

    def _make_box(self, msg, size):
        marker = Marker()
        marker.type = Marker.CUBE
        if None in size:
            size = [SkirosInteractiveMarkers.default_box_size, SkirosInteractiveMarkers.default_box_size, SkirosInteractiveMarkers.default_box_size]
        marker.scale.x = msg.scale * size[0]
        marker.scale.y = msg.scale * size[1]
        marker.scale.z = msg.scale * size[2]
        marker.color.r = 0.0
        marker.color.g = 0.0
        marker.color.b = 0.5
        marker.color.a = 0.5
        return marker

    def _make_box_control(self, msg, size):
        control = InteractiveMarkerControl()
        control.always_visible = True
        control.markers.append(self._make_box(msg, size))
        msg.controls.append(control)
        return control

    def initInteractiveServer(self, name):
        """
        @brief Start the interactive marker server
        """
        self._server = InteractiveMarkerServer(name)

    def clear_markers(self):
        self._server.clear()

    def make_6dof_marker(self, pose, size, frame_id, base_frame_id, interaction_mode):
        int_marker = InteractiveMarker()
        int_marker.header.frame_id = base_frame_id
        int_marker.pose.position.x = pose[0][0]
        int_marker.pose.position.y = pose[0][1]
        int_marker.pose.position.z = pose[0][2]
        int_marker.pose.orientation.x = pose[1][0]
        int_marker.pose.orientation.y = pose[1][1]
        int_marker.pose.orientation.z = pose[1][2]
        int_marker.pose.orientation.w = pose[1][3]
        int_marker.scale = 1

        int_marker.name = frame_id
        int_marker.description = frame_id

        # insert a box
        self._make_box_control(int_marker, size)
        int_marker.controls[0].interaction_mode = interaction_mode

        n = norm([1, 1])
        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 1 / n
        control.orientation.y = 0
        control.orientation.z = 0
        control.name = "rotate_x"
        control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
        int_marker.controls.append(control)

        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 1 / n
        control.orientation.y = 0
        control.orientation.z = 0
        control.name = "move_x"
        control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
        int_marker.controls.append(control)

        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 0
        control.orientation.y = 1 / n
        control.orientation.z = 0
        control.name = "rotate_y"
        control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
        int_marker.controls.append(control)

        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 0
        control.orientation.y = 1 / n
        control.orientation.z = 0
        control.name = "move_y"
        control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
        int_marker.controls.append(control)

        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 0
        control.orientation.y = 0
        control.orientation.z = 1 / n
        control.name = "rotate_z"
        control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
        int_marker.controls.append(control)

        control = InteractiveMarkerControl()
        control.orientation.w = 1 / n
        control.orientation.x = 0
        control.orientation.y = 0
        control.orientation.z = 1 / n
        control.name = "move_z"
        control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
        int_marker.controls.append(control)

        self._server.insert(int_marker, self.on_marker_feedback)
        self._server.applyChanges()


class SkirosWidget(QWidget, SkirosInteractiveMarkers):
    #==============================================================================
    #  General
    #==============================================================================

    widget_id = 'skiros_gui'

    wm_update_signal = pyqtSignal(msgs.WmMonitor)
    task_progress_signal = pyqtSignal(msgs.SkillProgress)

    def __init__(self):
        super(SkirosWidget, self).__init__()
        self.setObjectName('SkirosWidget')
        ui_file = os.path.join(rospkg.RosPack().get_path('skiros2_gui'), 'src/skiros2_gui/core', 'skiros_gui.ui')
        loadUi(ui_file, self)

        self.skill_tree_widget.currentItemChanged.connect(lambda: self.on_skill_tree_widget_item_selection_changed(self.skill_tree_widget.currentItem()))
        self.wm_tree_widget.itemSelectionChanged.connect(lambda: self.on_wm_tree_widget_item_selection_changed(self.wm_tree_widget.currentItem()))
        self.wm_properties_widget.itemChanged.connect(lambda p: self.on_properties_table_item_changed(self.wm_tree_widget.currentItem(), p.row()))
        self.wm_relations_widget.resizeEvent = self.on_wm_relations_widget_resized
        self.wm_update_signal.connect(lambda d: self.on_wm_update(d))
        self.task_progress_signal.connect(lambda d: self.on_progress_update(d))

        self.tableWidget_output.setColumnWidth(0, 60)
        self.tableWidget_output.setColumnWidth(1, 120)
        self.tableWidget_output.setColumnWidth(2, 120)
        self.tableWidget_output.setColumnWidth(3, 60)
        self.tableWidget_output.setColumnWidth(4, 40)
        self.reset()

    def reset(self):
        # The plugin should not call init_node as this is performed by rqt_gui_py.
        # Due to restrictions in Qt, you cannot manipulate Qt widgets directly within ROS callbacks,
        # because they are running in a different thread.
        self.initInteractiveServer(SkirosWidget.widget_id)
        self._wmi = wmi.WorldModelInterface(SkirosWidget.widget_id)
        self._sli = sli.SkillLayerInterface(SkirosWidget.widget_id)

        # Setup a timer to keep interface updated
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_timer_cb)
        self.refresh_timer.start(500)
        self.robot_sub = rospy.Subscriber('/robot_output', String, self.robot_output_cb)
        self.robot_text = ""


        # World model tab
        self._wmi.set_monitor_cb(lambda d: self.wm_update_signal.emit(d))
        self._sli.set_monitor_cb(lambda d: self.task_progress_signal.emit(d))
        self._snapshot_id = ""
        self._snapshot_stamp = rospy.Time.now()
        self._wm_mutex = Lock()
        self._task_mutex = Lock()

        # Skill tab
        self.last_executed_skill = ""
        self.skill_stop_button.setEnabled(False)
        self.space_shortcut = QShortcut(QtGui.QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.activated.connect(self.skill_start_stop)
        # self.space_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        # Log tab
        self.log_file = None
        self.icons = dict()


    def shutdown_plugin(self):
        with self._wm_mutex and self._task_mutex:
            self._wmi.set_monitor_cb(None)
            self._sli.set_monitor_cb(None)
            del self._sli
            del self._wmi

    def save_settings(self, plugin_settings, instance_settings):
        # TODO save intrinsic configuration, usually using:
        instance_settings.set_value("scene_name", self.scene_file_lineEdit.text())
        instance_settings.set_value("save_logs", self.save_logs_checkBox.isChecked())
        instance_settings.set_value("logs_file_name", self.logs_file_lineEdit.text())
        instance_settings.set_value("last_executed_skill", self.last_executed_skill)

    def restore_settings(self, plugin_settings, instance_settings):
        # TODO restore intrinsic configuration, usually using:
        if self._wmi.get_scene_name() != "":
            self.scene_file_lineEdit.setText(self._wmi.get_scene_name())
        elif instance_settings.value("scene_name") is not None and instance_settings.value("scene_name") != "":
            self.scene_file_lineEdit.setText(instance_settings.value("scene_name"))
        if instance_settings.value("logs_file_name") is not None:
            self.logs_file_lineEdit.setText(instance_settings.value("logs_file_name"))
        if instance_settings.value("save_logs") is not None:
            self.save_logs_checkBox.setChecked(instance_settings.value("save_logs") == 'true')
            self.on_save_logs_checkBox_clicked()
        if instance_settings.value("last_executed_skill") is not None:
            self.last_executed_skill = instance_settings.value("last_executed_skill")
            print(self.last_executed_skill)

    def trigger_configuration(self):
        # Comment in to signal that the plugin has a way to configure
        # This will enable a setting button (gear icon) in each dock widget title bar
        # Usually used to open a modal configuration dialog
        pass


#==============================================================================
#  General
#==============================================================================

    def robot_output_cb(self, msg):
        self.robot_text = msg.data

    def refresh_timer_cb(self):
        """
        Keeps ui updated
        """
        # Update skill list
        if self._sli.has_changes:
            self.skill_tree_widget.setSortingEnabled(False)
            self.skill_tree_widget.sortByColumn(0, Qt.AscendingOrder)
            self.skill_tree_widget.clear()
            self.skill_tree_widget.setColumnCount(3)
            self.skill_tree_widget.hideColumn(2)
            self.skill_tree_widget.hideColumn(1)
            fu = QTreeWidgetItem(self.skill_tree_widget, ["Frequently used", "fu"])
            fu.setExpanded(True)
            root = QTreeWidgetItem(self.skill_tree_widget, ["All", "All"])
            root.setExpanded(True)
            for ak, e in self._sli._agents.iteritems():
                for s in e._skill_list.values():
                    s.manager = ak
                    self._add_available_skill(s)
            # simplifies hierarchy
            self.simplify_tree_hierarchy(root)
            self.skill_tree_widget.setSortingEnabled(True)
            # select last skill
            s = self.skill_tree_widget.findItems(self.last_executed_skill, Qt.MatchRecursive | Qt.MatchFixedString, 1)
            self.skill_params_table.setRowCount(0)
            if s:
                self.skill_tree_widget.setCurrentItem(s[0])
        # Update robot BT rate
        if self._sli.agents:
            robot_info = ""
            for name, manager in self._sli.agents.iteritems():
                robot_info += "{}: {:0.1f}hz ".format(name.replace("/", ""), manager.get_tick_rate())
            self.robot_rate_info.setText(robot_info)
            self.robot_output.setText(self.robot_text)
        else:
            self.robot_rate_info.setText("No robot connected.")
            self.robot_output.setText("")


    def simplify_tree_hierarchy(self, root):
        i = 0
        while i < root.childCount():
            c = root.child(i)
            if c.childCount()==1:
                root.addChildren(c.takeChildren())
                root.removeChild(c)
            else:
                self.simplify_tree_hierarchy(c)
                i+=1

#==============================================================================
#  World model tab
#==============================================================================

    @Slot()
    def on_load_scene_button_clicked(self):
        file = self.scene_file_lineEdit.text()
        log.debug(self.__class__.__name__, 'Loading world model from <{}>'.format(file))
        self._wmi.load(file)

    @Slot()
    def on_save_scene_button_clicked(self):
        file = self.scene_file_lineEdit.text()
        log.debug(self.__class__.__name__, 'Saving world model to <{}>'.format(file))
        self._wmi.save(file)

    @Slot()
    def on_add_object_button_clicked(self):
        dialog = SkirosAddObjectDialog(self)
        ret = dialog.exec_()
        if not ret:
            return

        log.debug(self.__class__.__name__, 'Create element based on {}'.format(dialog.object))

        parent = self.wm_tree_widget.currentItem()
        parent_id = parent.text(1)
        if not parent_id:
            return

        elem = self._wmi.get_template_element(dialog.object)
        elem.label = utils.ontology_type2name(dialog.object)
        elem_id = self._wmi.instanciate(elem, recursive=True, relations=[{'src': parent_id, 'type': 'skiros:contain', 'dst': '-1'}])

        # parent = self.wm_tree_widget.currentItem()
        # parent_id = parent.text(1)
        log.debug(self.__class__.__name__, 'Added element {} to {}'.format(elem_id, parent_id))

        # item = QTreeWidgetItem(parent, [utils.ontology_type2name(elem_id), elem_id])
        # self.wm_tree_widget.setCurrentItem(item)

    @Slot()
    def on_remove_object_button_clicked(self):
        item = self.wm_tree_widget.currentItem()
        item_id = item.text(1)
        if not item_id:
            return
        parent = item.parent()
        self.wm_tree_widget.setCurrentItem(parent)

        elem = self._wmi.get_element(item_id)
        self._wmi.remove_element(elem)

        # parent = self.wm_tree_widget.currentItem()
        # parent_id = parent.text(1)
        log.debug(self.__class__.__name__, 'Removed element {}'.format(item_id))

        #item = self.wm_tree_widget.currentItem()
        # self.remove_wm_tree_widget_item(item)
        # parent.removeChild(item)

    # def remove_wm_tree_widget_item(self, item):
    #     if hasattr(item, 'id'):
    #         log.debug(self.__class__.__name__, 'Removing item: <{}>'.format(item.text(0)))
    #         elem = self._wmi.get_element(item.text(1))
    #         self._wmi.remove_element(elem)
    #     else:
    #         [self.remove_wm_tree_widget_item(child) for child in item.takeChildren()]

    @Slot()
    def on_wm_update(self, data):
        with self._wm_mutex:
            if self._snapshot_id == data.prev_snapshot_id and data.action != "reset":  # Discard msgs not in sync with local wm version
                self._snapshot_id = data.snapshot_id
                cur_item = self.wm_tree_widget.currentItem()
                cur_item_id = cur_item.text(1)
                if data.action == 'update' or data.action == 'update_properties':
                    for elem in data.elements:
                        elem = rosutils.msg2element(elem)
                        self._update_wm_node(elem, cur_item_id)
                elif data.action == 'add':
                    for elem in data.elements:
                        elem = rosutils.msg2element(elem)
                        if not self._add_wm_node(elem):
                            self._snapshot_id = ""
                elif data.action == 'remove' or data.action == 'remove_recursive':
                    for elem in data.elements:
                        elem = rosutils.msg2element(elem)
                        self._remove_wm_node(elem)
                # reselect current item
                items = self.wm_tree_widget.findItems(cur_item_id, Qt.MatchRecursive | Qt.MatchFixedString, 1)
                if items:
                    self.wm_tree_widget.setCurrentItem(items[0])
                #self._save_log(data, "wm_edit")
            elif data.stamp > self._snapshot_stamp or self._snapshot_id == "":  # Ignores obsolete msgs
                log.info("[wm_update]", "Wm not in sync, querying wm scene")
                self.create_wm_tree()

    def on_marker_feedback(self, feedback):
        if feedback.event_type == InteractiveMarkerFeedback.POSE_UPDATE:
            with self._wm_mutex:
                elem = self._wmi.get_element(feedback.marker_name)
                elem.setData(":PoseStampedMsg", feedback)
                self._wmi.update_element_properties(elem)

    @Slot()
    def on_wm_tree_widget_item_selection_changed(self, item):
        self.wm_properties_widget.blockSignals(True)
        self.clear_markers()
        if item.text(1).find("_skills") < 0:
            elem = self._wmi.get_element(item.text(1))
            self.fill_properties_table(elem)
            self.fill_relations_table(elem)
            if elem.hasProperty("skiros:DiscreteReasoner", "AauSpatialReasoner"):
                p = elem.getData(":Pose")
                size = elem.getData(":Size")
                if not None in p[0] and not None in p[1]:
                    self.make_6dof_marker(p, size, elem.id, elem.getProperty("skiros:BaseFrameId").value, InteractiveMarkerControl.NONE)  # NONE,MOVE_3D, MOVe_ROTATE_3D
        else:
            self.wm_properties_widget.setRowCount(0)
            self.wm_relations_widget.setRowCount(0)
        self.wm_properties_widget.blockSignals(False)

    @Slot()
    def on_properties_table_item_changed(self, item, row):
        item_key = self.wm_properties_widget.item(row, 0)
        item_val = self.wm_properties_widget.item(row, 1)
        key = item_key.id
        value = item_val.text()
        elem = self._wmi.get_element(item.text(1))

        if elem.hasProperty(key):
            prop = elem.getProperty(key)
            if value == '':
                value = None
            if prop.dataTypeIs(bool):
                value = item_val.checkState() == Qt.Checked
            try:
                elem.setProperty(prop.key, value, is_list=prop.isList(), force_convertion=value is not None)
                log.debug(self.__class__.__name__, '<{}> property {} to {}'.format(item.text(1), prop.key, value))
            except ValueError:
                log.error(self.__class__.__name__, 'Changing <{}> property {} to {} failed'.format(item.text(1), prop.key, value))
                item_val.setText(str(prop.value))
        elif hasattr(elem, key.lower()):
            key = key.lower()
            attr = getattr(elem, key)
            setattr(elem, key, type(attr)(value))
            log.debug(self.__class__.__name__, '<{}> attribute {} to {}'.format(item.text(1), key, value))
        else:
            log.error(self.__class__.__name__, 'Changing <{}> property {} to {} failed'.format(item.text(1), key, value))

        self._wmi.update_element_properties(elem)
        name = utils.ontology_type2name(elem.id) if not elem.label else utils.ontology_type2name(elem.label)
        item.setText(0, name)

    @Slot()
    def on_wm_relations_widget_resized(self, event):
        width = self.wm_relations_widget.size().width() - 2
        cols = self.wm_relations_widget.columnCount()
        for i in range(cols):
            self.wm_relations_widget.setColumnWidth(i, float(width) / cols)

    def create_wm_tree(self):
        scene_tuple = None
        while scene_tuple is None:
            try:
                scene_tuple = self._wmi.get_scene()
            except wmi.WmException:
                log.warn("[create_wm_tree]", "Failed to retrive scene, will try again.")
        #print "GOT SCENE {}".format([e.id for e in scene_tuple[0]])
        self._snapshot_id = scene_tuple[1]
        self._snapshot_stamp = rospy.Time.now()
        scene = {elem.id: elem for elem in scene_tuple[0]}
        root = scene['skiros:Scene-0']
        self.wm_tree_widget.clear()
        self.wm_tree_widget.setColumnCount(2)
        self.wm_tree_widget.hideColumn(1)
        self._create_wm_tree(self.wm_tree_widget, scene, root)
        self.wm_tree_widget.setCurrentIndex(self.wm_tree_widget.model().index(0, 0))

    def _remove_wm_node(self, elem):
        #print "Removing {}".format(elem.id)
        items = self.wm_tree_widget.findItems(elem.id, Qt.MatchRecursive | Qt.MatchFixedString, 1)
        if items:
            item = items[0]
            item.parent().removeChild(item)

    def _update_wm_node(self, elem, cur_item_id):
        # check if element is already in tree
        items = self.wm_tree_widget.findItems(elem.id, Qt.MatchRecursive | Qt.MatchFixedString, 1)
        if not items:
            return
        item = items[0]
        # check if updated item is selected
        if elem.id == cur_item_id:
            # update properties table if selected item has changed
            self.wm_properties_widget.blockSignals(True)
            self.fill_properties_table(elem)
            self.wm_properties_widget.blockSignals(False)

        # get parent node in tree
        parent = item.parent()
        if not parent:
            return

        # check if the old parent is still parent of the updated element
        parent_rel = elem.getRelation(pred=self._wmi.get_sub_properties('skiros:spatiallyRelated'), obj='-1')
        if not parent.text(1) in parent_rel['src']:
            # elem moved spatially
            item.parent().removeChild(item)
            parents = self.wm_tree_widget.findItems(parent_rel['src'], Qt.MatchRecursive | Qt.MatchFixedString, 1)
            if not parents:
                log.warn("[update_wm_node]", "No parent found for {}".format(elem.id))
                return
            item.setText(0, utils.ontology_type2name(elem.id) if not elem.label else utils.ontology_type2name(elem.label))
            item.setText(1, elem.id)
            parents[0].addChild(item)

    def _add_wm_node(self, elem):
        #print "Adding {}".format(elem.id)
        parent_rel = elem.getRelation(pred=self._wmi.get_sub_properties('skiros:spatiallyRelated'), obj='-1')
        to_expand = True
        if not parent_rel:
            parent_rel = elem.getRelation(pred=self._wmi.get_sub_properties('skiros:skillProperty'), obj='-1')
            to_expand = False
        if not parent_rel:
            to_expand = False
            parent_rel = elem.getRelation(pred='skiros:hasSkill', obj='-1')
            if not parent_rel:
                log.warn("[add_wm_node]", "Skipping element without declared parent: {}".format(elem.id))
                return True
            parent_id = '{}_skills'.format(parent_rel['src'])
            item = self.wm_tree_widget.findItems(parent_id, Qt.MatchRecursive | Qt.MatchFixedString, 1)
            if not item:  # In case it is still not existing i create the "support" skill node
                item = self.wm_tree_widget.findItems(parent_rel['src'], Qt.MatchRecursive | Qt.MatchFixedString, 1)[0]
                item = QTreeWidgetItem(item, ['Skills', parent_id])
            else:
                item = item[0]
        else:
            items = self.wm_tree_widget.findItems(parent_rel['src'], Qt.MatchRecursive | Qt.MatchFixedString, 1)
            if not items:
                log.warn("[add_wm_node]", "Parent {} of node {} is not in the known tree.".format(parent_rel['src'], elem.id))
                return False
            item = items[0]
        name = utils.ontology_type2name(elem.id) if not elem.label else utils.ontology_type2name(elem.label)
        item = QTreeWidgetItem(item, [name, elem.id])
        item.setExpanded(to_expand)
        return True

    def _create_wm_tree(self, item, scene, elem):
        name = utils.ontology_type2name(elem.id) if not elem.label else utils.ontology_type2name(elem.label)
        item = QTreeWidgetItem(item, [name, elem.id])

        spatialRel = sorted(elem.getRelations(subj='-1', pred=self._wmi.get_sub_properties('skiros:spatiallyRelated')), key=lambda r: r['dst'])
        for rel in spatialRel:
            if rel['dst'] in scene:
                self._create_wm_tree(item, scene, scene[rel['dst']])
                item.setExpanded(True)

        skillRel = sorted(elem.getRelations(subj='-1', pred='skiros:hasSkill'), key=lambda r: r['dst'])
        if skillRel:
            skillItem = QTreeWidgetItem(item, ['Skills', '{}_skills'.format(elem.id)])
            for rel in skillRel:
                if rel['dst'] in scene:
                    self._create_wm_tree(skillItem, scene, scene[rel['dst']])
                    skillItem.setExpanded(True)

        skillPropRel = sorted(elem.getRelations(subj='-1', pred=self._wmi.get_sub_properties('skiros:skillProperty')), key=lambda r: r['dst'])
        for rel in skillPropRel:
            if rel['dst'] in scene:
                self._create_wm_tree(item, scene, scene[rel['dst']])

    def fill_properties_table(self, elem):
        self.wm_properties_widget.setRowCount(0)
        type = elem.id[:elem.id.rfind('-')]
        id = elem.id[elem.id.rfind('-') + 1:]
        self._add_properties_table_row(Property('ID', id), editable_value=False)
        self._add_properties_table_row(Property('Type', type), editable_value=False)
        self._add_properties_table_row(Property('Label', elem.label), editable_value=True)
        props = sorted(elem.properties, key=lambda e: e.key)
        [self._add_properties_table_row(p, False, True) for p in props]

    def _add_properties_table_row(self, prop, editable_key=False, editable_value=True):
        key = QTableWidgetItem(utils.ontology_type2name(prop.key))
        if not editable_key:
            key.setFlags(key.flags() & ~Qt.ItemIsEditable)
        value = prop.values if prop.isList() else prop.value
        if prop.dataTypeIs(float):
            val = QTableWidgetItem(format(value, '.4f') if value is not None else '')
        else:
            val = QTableWidgetItem(str(value) if value is not None else '')
        if not editable_value:
            val.setFlags(val.flags() & ~Qt.ItemIsEditable)

        if prop.dataTypeIs(bool):
            val.setText('')
            val.setFlags(val.flags() & ~Qt.ItemIsEditable)
            val.setCheckState(Qt.Checked if prop.value else Qt.Unchecked)
        # if isinstance(prop.dataType(), bool):
        #     val.setCheckState(prop.value)

        key.id = val.id = prop.key

        self.wm_properties_widget.insertRow(self.wm_properties_widget.rowCount())
        self.wm_properties_widget.setItem(self.wm_properties_widget.rowCount() - 1, 0, key)
        self.wm_properties_widget.setItem(self.wm_properties_widget.rowCount() - 1, 1, val)

    def fill_relations_table(self, elem):
        self.wm_relations_widget.setRowCount(0)
        rel = sorted(elem.getRelations(), key=lambda r: r['type'])
        rel = sorted(rel, key=lambda r: r['src'], reverse=True)
        rel = map(lambda r: {
            'src': r['src'] if r['src'] != '-1' else elem.id,
            'type': r['type'],
            'dst': r['dst'] if r['dst'] != '-1' else elem.id
        }, rel)
        [self._add_relations_table_row(r, r['src'] != elem.id, r['dst'] != elem.id) for r in rel]

    def _add_relations_table_row(self, relation, editable_src=False, editable_dst=False):
        src = QTableWidgetItem(utils.ontology_type2name(relation['src']))
        rel = QTableWidgetItem(utils.ontology_type2name(relation['type']))
        dst = QTableWidgetItem(utils.ontology_type2name(relation['dst']))

        src.id = relation['src']
        rel.id = relation['type']
        dst.id = relation['dst']

        src.setTextAlignment(Qt.AlignRight)
        rel.setTextAlignment(Qt.AlignHCenter)
        dst.setTextAlignment(Qt.AlignLeft)

        if not editable_src:
            src.setFlags(src.flags() & ~Qt.ItemIsEditable)
        rel.setFlags(rel.flags() & ~Qt.ItemIsEditable)
        if not editable_dst:
            dst.setFlags(dst.flags() & ~Qt.ItemIsEditable)

        self.wm_relations_widget.insertRow(self.wm_relations_widget.rowCount())
        # self.wm_relations_widget.setSpan(self.wm_relations_widget.rowCount()-1, 0, 1, 3)
        self.wm_relations_widget.setItem(self.wm_relations_widget.rowCount() - 1, 0, src)
        self.wm_relations_widget.setItem(self.wm_relations_widget.rowCount() - 1, 1, rel)
        self.wm_relations_widget.setItem(self.wm_relations_widget.rowCount() - 1, 2, dst)

    @Slot('QTreeWidgetItem*', 'QTreeWidgetItem*')
    def wm_tree_widget_currentItemChanged(self, item, prev_item):
        while self.wm_properties_widget.rowCount() > 0:
            self.wm_properties_widget.removeRow(0)
        item = QTableWidgetItem(str(item.text(0)))
        self.wm_properties_widget.insertRow(0)
        self.wm_properties_widget.setItem(0, 0, item)
        while self.wm_relations_widget.rowCount() > 0:
            self.wm_relations_widget.removeRow(0)
        item = QTableWidgetItem("")
        self.wm_relations_widget.insertRow(0)
        self.wm_relations_widget.setItem(0, 0, item)


#==============================================================================
# Skill
#==============================================================================
    def _update_progress_table(self, msg):
        last_msg = self.tableWidget_output.item(self.tableWidget_output.rowCount() - 1, 5).text() if self.tableWidget_output.rowCount() > 0 else ""
        if msg.progress_message != "Start" and (msg.label.find("task") >= 0 or (msg.progress_message != "End" and msg.progress_message != last_msg)):
            self.tableWidget_output.insertRow(self.tableWidget_output.rowCount())
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 0, QTableWidgetItem("{:0.3f}".format(msg.progress_time)))
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 1, QTableWidgetItem("{}_{}".format(msg.parent_label, msg.parent_id)))
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 2, QTableWidgetItem("{}_{}".format(msg.label, msg.id)))
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 3, QTableWidgetItem(State(msg.state).name))
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 4, QTableWidgetItem(str(msg.progress_code)))
            progress = QTableWidgetItem(str(msg.progress_message))
            progress.setToolTip(str(msg.progress_message))
            self.tableWidget_output.setItem(self.tableWidget_output.rowCount() - 1, 5, progress)
            self.tableWidget_output.scrollToBottom()

    @Slot()
    def on_progress_update(self, msg):
        self._update_progress_table(msg)
        self._save_log(msg, "skill")
        # Update buttons
        if msg.type.find("Root") >= 0:
            if not self.skill_stop_button.isEnabled():
                self.create_task_tree(msg.id, msg.processor)
                self._toggle_task_active()
                for manager in self._sli.agents.values():
                    manager.reset_tick_rate()
            if abs(msg.progress_code) == 1:
                self._toggle_task_active()
        # Update task tree
        with self._task_mutex:
            items = self.task_tree_widget.findItems(str(msg.id), Qt.MatchRecursive | Qt.MatchFixedString, 1)
            #int(msg.progress_period*1000)
            if items:
                items[0].setData(0, 0, "{}({}) {}".format(msg.label, State(msg.state).name, "! SLOW !" if msg.progress_period>0.04 else ""))
            else:
                parents = self.task_tree_widget.findItems(str(msg.parent_id), Qt.MatchRecursive | Qt.MatchFixedString, 1)
                if not parents:
                    log.error("No parent found. Debug: {}".format(msg))
                    return
                item = QTreeWidgetItem(parents[0], ["{}({})".format(msg.label, State(msg.state).name), str(msg.id)])
                item.setIcon(0, self.get_icon(msg.processor))
                item.setExpanded(True)

    def get_icon(self, skill_type):
        if not skill_type in self.icons:
            file_name = os.path.join(rospkg.RosPack().get_path("skiros2_gui"), "src/skiros2_gui/core/imgs/", "{}.png".format(skill_type if skill_type else "skill"))
            self.icons[skill_type] = QtGui.QIcon(file_name)
        return self.icons[skill_type]

    def create_task_tree(self, task_id, processor):
        self.task_tree_widget.clear()
        self.task_tree_widget.setColumnCount(2)
        self.task_tree_widget.hideColumn(1)
        item = QTreeWidgetItem(self.task_tree_widget, ["Task {}".format(task_id), str(task_id)])
        item.setExpanded(True)
        return item

    def _get_parameters(self, params):
        layout = self.skill_params_table
        for i in range(0, layout.rowCount()):
            key = layout.item(i, 0).text()
            widget = layout.cellWidget(i, 1)
            if params[key].dataTypeIs(bool):
                params[key].setValue(widget.isChecked())
            elif params[key].dataTypeIs(Element):
                data = widget.itemData(widget.currentIndex())
                if data:
                    params[key].setValue(self._wmi.get_element(data))
                    #print "Set param {} to {}".format(params[key].key, params[key].value.printState())
            else:
                try:
                    if widget.text():
                        params[key].setValueFromStr(widget.text())
                except ValueError:
                    log.error("getParameters", "Failed to set param {}".format(params[key].key))
                    return False
        return True

    def _add_parameter(self, param):
        key = QTableWidgetItem(param.key)
        key.setFlags(key.flags() & ~Qt.ItemIsEditable)
        row = self.skill_params_table.rowCount()
        self.skill_params_table.insertRow(row)
        #row = row-1
        self.skill_params_table.setItem(row, 0, key)
        if param.dataTypeIs(bool):
            cbox = QCheckBox()
            if param.hasSpecifiedDefault():
                cbox.setChecked(param.default)
            self.skill_params_table.setCellWidget(row, 1, cbox)
        elif param.dataTypeIs(Element):
            combobox = QComboBox()
            self.skill_params_table.setCellWidget(row, 1, combobox)
            matches = self._wmi.resolve_elements(param.default)
            if param.paramTypeIs(ParamTypes.Optional):
                combobox.addItem("", None)
            for e in matches:
                combobox.addItem(e.printState(), e._id)
        else:
            lineedit = QLineEdit()
            if param.isSpecified():
                lineedit.setText(str(param.value))
            self.skill_params_table.setCellWidget(row, 1, lineedit)

    def _add_available_skill(self, s):
        stype = self.skill_tree_widget.findItems(s.type, Qt.MatchRecursive | Qt.MatchFixedString, 1)
        if not stype: #If it is the first of its type, add the parents hierarchy to the tree
            hierarchy = self._wmi.query_ontology('SELECT ?x {{ {} rdfs:subClassOf*  ?x }}'.format(s.type))
            hierarchy = hierarchy[:hierarchy.index("skiros:Skill")]
            hierarchy.reverse()
            parent = self.skill_tree_widget.findItems("All", Qt.MatchRecursive | Qt.MatchFixedString, 1)[0]
            for c in hierarchy:
                child = self.skill_tree_widget.findItems(c, Qt.MatchRecursive | Qt.MatchFixedString, 1)
                if child:
                    parent = child[0]
                else:
                    parent = QTreeWidgetItem(parent, [c.replace("skiros:", ""), c])
                    parent.setExpanded(True)
        else:
            parent = stype[0]
        skill = QTreeWidgetItem(parent, [s.name, s.name])
        skill.setData(2, 0, s)

    def _add_frequently_used_skill(self, s):
        self.last_executed_skill = s.name
        fu = self.skill_tree_widget.findItems("fu", Qt.MatchRecursive | Qt.MatchFixedString, 1)[0]
        for i in range(0, fu.childCount()):  # avoid adding same node multiple times
            if s.name == fu.child(i).data(1, 0):
                return
        skill = QTreeWidgetItem(fu, [s.name, s.name])
        skill.setData(2, 0, s)

    def _toggle_task_active(self):
        if self.skill_stop_button.isEnabled():
            self.skill_stop_button.setEnabled(False)
            self.skill_exe_button.setEnabled(True)
        else:
            self.skill_stop_button.setEnabled(True)
            self.skill_exe_button.setEnabled(False)

    @Slot()
    def skill_start_stop(self):
        if self.skill_stop_button.isEnabled():
            self.on_skill_stop_button_clicked()
        else:
            self.on_skill_exe_button_clicked()

    @Slot()
    def on_skill_tree_widget_item_selection_changed(self, item):
        if item is None:
            return
        skill = item.data(2, 0)
        if skill:
            # Clean
            self.skill_params_table.setRowCount(0)
            # Add params
            self.skill_name_label.setText(skill.name)
            for p in skill.ph.values():
                if not p.paramTypeIs(ParamTypes.Inferred) and (self.modality_checkBox.isChecked() or p.paramTypeIs(ParamTypes.Required)):
                    self._add_parameter(p)
            self.skill_params_table.resizeRowsToContents()

    @Slot()
    def on_modality_checkBox_clicked(self):
        self.on_skill_tree_widget_item_selection_changed(self.skill_tree_widget.currentItem())

    @Slot()
    def on_skill_exe_button_clicked(self):
        if self.skill_tree_widget.currentItem() is None:
            return
        skill = deepcopy(self.skill_tree_widget.currentItem().data(2, 0))
        if skill is None:
            return
        self._add_frequently_used_skill(self.skill_tree_widget.currentItem().data(2, 0))
        # Start logger
        if not self._sli.has_active_tasks:
            prefix = self.logs_file_lineEdit.text()
            prefix = prefix[0:prefix.rfind("/")]
            self.logs_file_lineEdit.setText("{}/{}_{}".format(prefix, datetime.now().strftime("%Y-%m-%d:%H:%M:%S"), skill.name))
            self.on_save_logs_checkBox_clicked()
        # Send command
        if not self._get_parameters(skill.ph):
            return
        self._sli.execute(skill.manager, [skill])

    @Slot()
    def on_skill_stop_button_clicked(self):
        if not self._sli.preempt_one():
            log.error("", "Nothing to preempt.")

#==============================================================================
# Logs
#==============================================================================
    @Slot()
    def on_logs_file_lineEdit_editingFinished(self):
        self.on_save_logs_checkBox_clicked()

    def on_save_logs_checkBox_clicked(self):
        if self.log_file is not None:
            self.logs_textEdit.clear()
            self.log_file.close()
            self.log_file = None
        if self.save_logs_checkBox.isChecked():
            file_name = os.path.expanduser(self.logs_file_lineEdit.text())
            directory = file_name[0: file_name.rfind('/')]
            if not os.path.exists(directory):
                os.makedirs(directory)
            elif os.path.exists(file_name):
                with open(file_name, "r") as f:
                    self.logs_textEdit.setText(f.read())
            self.log_file = open(file_name, "a")

    def _save_log(self, msg, log_type):
        if log_type == "skill":
            string = "{};{:0.4f};{};{};{};{};{};{};{}".format(datetime.now().strftime("%H:%M:%S"), msg.progress_time, msg.parent_label, msg.parent_id,
                                                              msg.label, msg.id, State(msg.state).name, msg.progress_code, msg.progress_message)
        elif log_type == "wm_edit":
            if not msg.relation:
                string = "{} {} {} {}".format(datetime.now(), "WM", msg.action, [e.id for e in msg.elements])
            else:
                relation = rosutils.msg2relation(msg.relation[0])
                string = "{} {} {}_relation {}-{}-{}".format(datetime.now().strftime("%H:%M:%S"), "WM", msg.action, relation['src'], relation['type'], relation['dst'])
        if self.save_logs_checkBox.isChecked():
            self.logs_textEdit.append(string)
            self.log_file.write(string + "\n")
