import rospy
import skiros2_msgs.msg as msgs
import skiros2_msgs.srv as srvs
from std_msgs.msg import Empty
import skiros2_common.ros.utils as utils
from skiros2_skill.ros.utils import SkillHolder
import skiros2_common.tools.logger as log
from multiprocessing import Lock, Event
import rostopic


class SkillManagerInterface:
    def __init__(self, manager_name):
        self._skill_mgr_name = manager_name
        self._active_tasks = list()
        self._module_list = dict()
        self._skill_list = dict()
        self._msg_lock = Lock()
        self._msg_rec = Event()
        rospy.wait_for_service(self._skill_mgr_name + '/get_skills')
        self._skill_exe_client = rospy.ServiceProxy(self._skill_mgr_name + '/command', srvs.SkillCommand)
        self._get_skills = rospy.ServiceProxy(self._skill_mgr_name + '/get_skills', srvs.ResourceGetDescriptions)
        self._monitor_sub = rospy.Subscriber(self._skill_mgr_name + '/monitor', msgs.SkillProgress, self._progress_cb)
        self._tick_rate = rostopic.ROSTopicHz(50)
        self._tick_rate_sub = rospy.Subscriber(self._skill_mgr_name + '/tick_rate', Empty, self._tick_rate.callback_hz)
        self._monitor_cb = None
        self.get_skill_list(True)

    @property
    def name(self):
        return self._skill_mgr_name

    @property
    def active_tasks(self):
        return self._active_tasks

    @property
    def skills(self):
        """
        @brief Return the list of available skills
        """
        return self.get_skill_list(update=False)

    def shutdown(self):
        """
        @brief Unregister subscribers (note: deleting the instance without calling shutdown will leave callbacks active)
        """
        self._monitor_sub.unregister()
        self._tick_rate_sub.unregister()

    def print_state(self):
        temp = "Skills: { "
        for c in self.get_skill_list():
            temp += c
            temp += ", "
        temp += "}"
        return temp

    def get_skill_list(self, update=False):
        if update or not self._skill_list:
            msg = srvs.ResourceGetDescriptionsRequest()
            res = self.call(self._get_skills, msg)
            self._skill_list = dict()
            if not res:
                log.error("[{}]".format(self.__class__.__name__), "Can t retrieve skills.")
            else:
                for c in res.list:
                    self._skill_list[c.name] = SkillHolder(self.name, c.type, c.name, utils.deserializeParamMap(c.params))
        return self._skill_list

    def get_skill(self, name):
        return self._skill_list[name]

    def execute(self, skill_list, author):
        msg = srvs.SkillCommandRequest()
        msg.action = msg.START
        msg.author = author
        for s in skill_list:
            msg.skills.append(s.toMsg())
        res = self.call(self._skill_exe_client, msg)
        if res is None:
            return -1
        if not res.ok:
            log.error("Can t execute task ")
            return -1
        self._active_tasks.append(res.execution_id)
        return res.execution_id

    def preempt(self, author, execution_id=None):
        msg = srvs.SkillCommandRequest()
        msg.action = msg.PREEMPT
        msg.author = author
        if not self.active_tasks:
            return False
        if execution_id is None:
            execution_id = self.active_tasks[-1]
        msg.execution_id = execution_id
        res = self.call(self._skill_exe_client, msg)
        if res is None:
            return False
        elif not res.ok:
            log.error("Can t stop task " + execution_id)
            return False
        return True

    def preempt_all(self, author):
        msg = srvs.SkillCommandRequest()
        msg.action = msg.PREEMPT
        msg.author = author
        msg.execution_id = -1
        res = self.call(self._skill_exe_client, msg)
        if res is None:
            return False
        elif not res.ok:
            log.error("Can t stop tasks.")
            return False
        self._active_tasks = list()
        return True

    def set_monitor_cb(self, cb):
        """
        @brief Set an external callback on skill execution feedback
        """
        self._monitor_cb = cb

    def get_tick_rate(self):
        """
        @brief Get the skill manager tick rate
        """
        rate_info = self._tick_rate.get_hz()
        if rate_info is None:
            return 0
        return rate_info[0]

    def reset_tick_rate(self):
        """
        @brief Reset the tick rate information
        """
        pass  # self._tick_rate.set_msg_t0(rospy.get_rostime().to_sec())

    def _progress_cb(self, msg):
        if msg.type.find("Root") >= 0 and abs(msg.progress_code) == 1:
            try:
                self._active_tasks.remove(int(msg.task_id))
            except Exception:
                pass
        if self._monitor_cb:
            self._monitor_cb(msg)

    def call(self, service, msg):
        try:
            resp1 = service(msg)
            return resp1
        except rospy.ServiceException, e:
            log.error("[call]", "Service call failed: %s"%e)
            return
