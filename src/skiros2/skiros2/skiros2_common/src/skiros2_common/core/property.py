import skiros2_common.tools.logger as log
import ast


class Property(object):
    """
    @brief Touple key-value with datatype check

    If input doesn't correspond to the defined data type, it is refused

    Data type is set during initialization
    """
    __slots__ = ['_key', '_values', '_data_type', '_is_list']

    def __init__(self, key, value, is_list=False):
        """
        Value can be any value or list of values

        Value can also be a type, in such a case the _data_type is set and the value list is left empty
        """
        self._is_list = is_list
        self._key = key
        if isinstance(value, list):
            self._values = value
            self._data_type = type(value[0])
        elif isinstance(value, type):
            self._values = list()
            self._data_type = value
        else:
            self._values = [value]
            self._data_type = type(value)

    def isSpecified(self):
        """
        @brief Return true if the property has at least one value specified
        """
        return bool(self._values)

    def isList(self):
        """
        @brief Return true if the property can have more than one value
        """
        return self._is_list

    @property
    def key(self):
        return self._key

    @property
    def value(self):
        return self.getValue()

    @value.setter
    def value(self, value):
        self.setValue(value)

    @property
    def values(self):
        return self.getValues()

    @values.setter
    def values(self, value):
        self.setValues(value)

    def makeInstance(self):
        """
        @brief Return an instance of the property datatype
        """
        return self._data_type()

    def dataType(self):
        """
        @brief Return the property datatype
        """
        return self._data_type

    def unset(self):
        """
        @brief Unset the property (getValue will then return None)
        """
        self._values = list()

    def dataTypeIs(self, vtype):
        """
        @brief Return true if input type match the property datatype
        """
        if isinstance(vtype, type):
            return self._data_type == vtype
        else:
            return isinstance(vtype, self._data_type)

    def setValue(self, value, index=0):
        """
        @brief Set the value at the index
        """
        if isinstance(value, self._data_type):
            if self.isSpecified():
                self._values[index] = value
            else:
                self._values.append(value)
        else:
            log.error("setValue", "{}: Input {} != {}. Debug: {}".format(self.key, type(value), self._data_type, self.printState()))
            return

    def setValueFromStr(self, value, index=0):
        """
        @brief Try to convert a string into the property datatype and set the value at index
        """
        if self.dataTypeIs(dict):
            self.setValue(ast.literal_eval(value), index)
        else:
            self.setValue(self._data_type(value), index)

    def setValues(self, value):
        """
        @brief Set all the values
        """
        if isinstance(value, list):
            if len(value) == 0:
                self._values = list()
                return
            if isinstance(value[0], self._data_type):
                self._values = value
            else:
                log.error("setValuesList", "{}: Input {} != {} Debug: {}. Input: {}.".format(self.key, type(value[0]), self._data_type, self.printState(), value))
        elif isinstance(value, self._data_type):
            self._values = [value]
        elif value is None:
            self._values = list()
        else:
            log.error("setValues", "{}: Input {} != {}. Debug: {}".format(self.key, type(value), self._data_type, self.printState()))

    def removeValue(self, value):
        """
        @brief Removes the first value matching. Does nothing if value is not present
        """
        i = self.find(value)
        if i >= 0:
            del self._values[i]

    def find(self, value):
        """
        @brief Return the index of the value, or -1 if not found
        """
        for i, v in enumerate(self._values):
            if v == value:
                return i
        return -1

    def addValue(self, value):
        """
        @brief Append a value
        """
        if isinstance(value, self._data_type):
            self._values.append(value)
        else:
            log.error("append", self._key + ": " + str(type(value)) + "!=" + str(self._data_type))
            return

    def getValue(self, index=0):
        """
        @brief Get value at index
        """
        if not self.isSpecified():
            return None
        return self._values[index]

    def getValues(self):
        """
        @brief Get all values
        """
        return self._values

    def getValuesStr(self):
        """
        @brief Get values as string
        """
        return str(self._values)

    def printState(self):
        """
        @brief Return a string with key and values
        """
        v = str(self._values)
        max_lenght = 500
        return "{}:{}".format(self._key, v) if len(v) < max_lenght else "{}:{} ...]".format(self._key, v[0:max_lenght])
