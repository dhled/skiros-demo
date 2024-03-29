from skiros2_common.core.abstract_skill import SkillCore, State
import skiros2_common.tools.logger as log
from skiros2_common.core.world_element import Element
from datetime import datetime


class PrimitiveBase(SkillCore):
    """
    @brief Base class for primitive skills
    """
    #--------Control functions--------

    def tick(self):
        if self.hasState(State.Success) or self.hasState(State.Failure):
            log.error("tick", "Reset required before ticking.")
            return State.Failure
        elif not self.hasState(State.Running):
            log.error("tick", "Start required before ticking.")
            return State.Failure
        else:
            start_time = datetime.now()
            with self._time_keeper:
                self._setState(self.execute())
                self._updateRoutine(start_time)
#            if self._progress_msg!="":
#                log.info("[{}]".format(self.printState()), self._progress_msg)
            if self.hasState(State.Success) or self.hasState(State.Failure):
                if not self.onEnd():
                    self._setState(State.Failure)
            return self._state

    def init(self, wmi, _=None):
        self._wmi = wmi
        self.createDescription()
        self.generateDefParams()
        self.generateDefConditions()
        self.modifyDescription(self)
        return self.onInit()

    def _updateRoutine(self, time):
        """
        @brief Sync the modified parameters elements with wm
        @time The time to evaluate if a parameter was changed
        """
        for k, p in self.params.iteritems():
            if p.dataTypeIs(Element()) and p.hasChanges(time):
                vs = p.values
                for i, e in enumerate(vs):
                    if not e.isAbstract():
                        self._wmi.update_element(e)
                    else:
                        vs[i] = self._wmi.add_element(e)


    #--------Virtual functions--------
    def onInit(self):
        """Called once when loading the primitive. If return False, the primitive is not loaded"""
        return True

