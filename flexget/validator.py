from __future__ import unicode_literals, division, absolute_import
import re
from flexget.utils import qualities

# TODO: rename all validator.valid -> validator.accepts / accepted / accept ?


class Errors(object):
    """Create and hold validator error messages."""

    def __init__(self):
        self.messages = []
        self.path = []
        self.path_level = None

    def count(self):
        """Return number of errors."""
        return len(self.messages)

    def add(self, msg):
        """Add new error message to current path."""
        path = [unicode(p) for p in self.path]
        msg = '[/%s] %s' % ('/'.join(path), msg)
        self.messages.append(msg)

    def back_out_errors(self, num=1):
        """Remove last num errors from list"""
        if num > 0:
            del self.messages[0 - num:]

    def path_add_level(self, value='?'):
        """Adds level into error message path"""
        self.path_level = len(self.path)
        self.path.append(value)

    def path_remove_level(self):
        """Removes level from path by depth number"""
        if self.path_level is None:
            raise Exception('no path level')
        del(self.path[self.path_level])
        self.path_level -= 1

    def path_update_value(self, value):
        """Updates path level value"""
        if self.path_level is None:
            raise Exception('no path level')
        self.path[self.path_level] = value

# A registry mapping validator names to their class
registry = {}


def factory(name='root', **kwargs):
    """Factory method, returns validator instance."""
    if name not in registry:
        raise Exception('Asked unknown validator \'%s\'' % name)
    return registry[name](**kwargs)


def any_schema(schema_list):
    """
    Creates a schema that will match any of the given schemas.
    Will not use anyOf if there is just one validator in the list, for simpler error messages.
    """
    if len(schema_list) == 1:
        return schema_list[0]
    else:
        return {'anyOf': [s for s in schema_list]}



class Validator(object):
    name = 'validator'

    class __metaclass__(type):
        """Automatically adds subclasses to the registry."""

        def __init__(cls, name, bases, dict):
            type.__init__(cls, name, bases, dict)
            if not 'name' in dict:
                raise Exception('Validator %s is missing class-attribute name' % name)
            registry[dict['name']] = cls

    def __init__(self, parent=None, message=None, **kwargs):
        self.valid = []
        self.message = message
        self.parent = parent
        self._errors = None

    @property
    def errors(self):
        """Recursively return the Errors class from the root of the validator tree."""
        if self.parent:
            return self.parent.errors
        else:
            if not self._errors:
                self._errors = Errors()
            return self._errors

    def add_root_parent(self):
        if self.name == 'root':
            return self
        root = factory('root')
        root.accept(self)
        return root

    def add_parent(self, parent):
        self.parent = parent
        return parent

    def get_validator(self, value, **kwargs):
        """Returns a child validator of this one.

        :param value:
          Can be a validator type string, an already created Validator instance,
          or a function that returns a validator instance.
        :param kwargs:
          Keyword arguments are passed on to validator init if a new validator is created.
        """
        if isinstance(value, Validator):
            # If we are passed a Validator instance, make it a child of this validator and return it.
            value.add_parent(self)
            return value
        elif callable(value):
            # Create a LazyValidator that will serve as a Validator when attributes are accessed.
            return LazyValidator(value, parent=self)
        # Otherwise create a new child Validator
        kwargs['parent'] = self
        return factory(value, **kwargs)

    def accept(self, value, **kwargs):
        raise NotImplementedError('Validator %s should override accept method' % self.__class__.__name__)

    def validateable(self, data):
        """Return True if validator can be used to validate given data, False otherwise."""
        raise NotImplementedError('Validator %s should override validateable method' % self.__class__.__name__)

    def validate(self, data):
        """Validate given data and log errors, return True if passed and False if not."""
        raise NotImplementedError('Validator %s should override validate method' % self.__class__.__name__)

    def schema(self):
        """Return schema for validator"""
        raise NotImplementedError(self.__name__)

    def validate_item(self, item, rules):
        """
        Helper method. Validate item against list of rules (validators).
        Return True if item passed any of the rules, False if none of the rules pass item.
        """
        count = self.errors.count()
        for rule in rules:
            # print 'validating %s' % rule.name
            if rule.validateable(item):
                if rule.validate(item):
                    # item is valid, remove added errors before returning
                    self.errors.back_out_errors(self.errors.count() - count)
                    return True

        # If no validators matched or reported errors, and one of them has a custom error message, display it.
        if count == self.errors.count():
            for rule in rules:
                if rule.message:
                    self.errors.add(rule.message)
                # If there are still no errors, list the valid types, as well as what was actually received
            if count == self.errors.count():
                acceptable = [v.name for v in rules]
                # Make acceptable into an english list, with commas and 'or'
                acceptable = ', '.join(acceptable[:-2] + ['']) + ' or '.join(acceptable[-2:])
                self.errors.add('must be a `%s` value' % acceptable)
                if isinstance(item, dict):
                    self.errors.add('got a dict instead of %s' % acceptable)
                elif isinstance(item, list):
                    self.errors.add('got a list instead of %s' % acceptable)
                else:
                    self.errors.add('value \'%s\' is not valid %s' % (item, acceptable))
        return False

    def __str__(self):
        return '<validator:name=%s>' % self.name

    __repr__ = __str__


class RootValidator(Validator):
    name = 'root'

    def accept(self, value, **kwargs):
        v = self.get_validator(value, **kwargs)
        self.valid.append(v)
        return v

    def validateable(self, data):
        return True

    def validate(self, data):
        return self.validate_item(data, self.valid)

    def schema(self):
        return any_schema([v.schema() for v in self.valid])


class ChoiceValidator(Validator):
    name = 'choice'

    def __init__(self, parent=None, **kwargs):
        self.valid_ic = []
        Validator.__init__(self, parent, **kwargs)

    def accept(self, value, ignore_case=False):
        """
        :param value: accepted text, int or boolean
        :param bool ignore_case: Whether case matters for text values
        """
        if not isinstance(value, (basestring, int, float)):
            raise Exception('Choice validator only accepts strings and numbers')
        if isinstance(value, basestring) and ignore_case:
            self.valid_ic.append(value.lower())
        else:
            self.valid.append(value)

    def accept_choices(self, values, **kwargs):
        """Same as accept but with multiple values (list)"""
        for value in values:
            self.accept(value, **kwargs)

    def validateable(self, data):
        return isinstance(data, (basestring, int, float))

    def validate(self, data):
        if data in self.valid:
            return True
        elif isinstance(data, basestring) and data.lower() in self.valid_ic:
            return True
        else:
            acceptable = sorted(unicode(value) for value in self.valid + self.valid_ic)
            self.errors.add('\'%s\' is not one of acceptable values: %s' % (data, ', '.join(acceptable)))
            return False

    def schema(self):
        return {'enum': self.valid + self.valid_ic}


class AnyValidator(Validator):
    name = 'any'

    def accept(self, value, **kwargs):
        self.valid = value

    def validateable(self, data):
        return True

    def validate(self, data):
        return True

    def schema(self):
        return {}


class EqualsValidator(Validator):
    name = 'equals'

    def accept(self, value, **kwargs):
        self.valid = value

    def validateable(self, data):
        return isinstance(data, (basestring, int, float))

    def validate(self, data):
        return self.valid == data

    def schema(self):
        return {'enum': [self.valid]}


class NumberValidator(Validator):
    name = 'number'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, (int, float, long))

    def validate(self, data):
        valid = isinstance(data, (int, float, long))
        if not valid:
            self.errors.add('value %s is not valid number' % data)
        return valid

    def schema(self):
        return {'type': 'number'}


class IntegerValidator(Validator):
    name = 'integer'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, int)

    def validate(self, data):
        valid = isinstance(data, int)
        if not valid:
            self.errors.add('value %s is not valid integer' % data)
        return valid

    def schema(self):
        return {'type': 'integer'}


# TODO: Why would we need this instead of NumberValidator?
class DecimalValidator(Validator):
    name = 'decimal'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, float)

    def validate(self, data):
        valid = isinstance(data, float)
        if not valid:
            self.errors.add('value %s is not valid decimal number' % data)
        return valid

    def schema(self):
        return {'type': 'number'}


class BooleanValidator(Validator):
    name = 'boolean'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, bool)

    def validate(self, data):
        valid = isinstance(data, bool)
        if not valid:
            self.errors.add('value %s is not valid boolean' % data)
        return valid

    def schema(self):
        return {'type': 'boolean'}


class TextValidator(Validator):
    name = 'text'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, basestring)

    def validate(self, data):
        valid = isinstance(data, basestring)
        if not valid:
            self.errors.add('value %s is not valid text' % data)
        return valid

    def schema(self):
        return {'type': 'string'}


class RegexpValidator(Validator):
    name = 'regexp'

    def accept(self, name, **kwargs):
        pass

    def validateable(self, data):
        return isinstance(data, basestring)

    def validate(self, data):
        if not isinstance(data, basestring):
            self.errors.add('Value should be text')
            return False
        try:
            re.compile(data)
        except:
            self.errors.add('%s is not a valid regular expression' % data)
            return False
        return True

    def schema(self):
        return {'type': 'string', 'format': 'regex'}


class RegexpMatchValidator(Validator):
    name = 'regexp_match'

    def __init__(self, parent=None, **kwargs):
        Validator.__init__(self, parent, **kwargs)
        self.regexps = []
        self.reject_regexps = []

    def add_regexp(self, regexp_list, regexp):
        try:
            regexp_list.append(re.compile(regexp))
        except:
            raise ValueError('Invalid regexp given to match_regexp')

    def accept(self, regexp, **kwargs):
        self.add_regexp(self.regexps, regexp)
        if kwargs.get('message'):
            self.message = kwargs['message']

    def reject(self, regexp):
        self.add_regexp(self.reject_regexps, regexp)

    def validateable(self, data):
        return isinstance(data, basestring)

    def validate(self, data):
        if not isinstance(data, basestring):
            self.errors.add('Value should be text')
            return False
        for regexp in self.reject_regexps:
            if regexp.match(data):
                break
        else:
            for regexp in self.regexps:
                if regexp.match(data):
                    return True
        if self.message:
            self.errors.add(self.message)
        else:
            self.errors.add('%s does not match regexp' % data)
        return False

    def schema(self):
        schema = any_schema([{'type': 'string', 'pattern': regexp.pattern} for regexp in self.regexps])
        if self.reject_regexps:
            schema['not'] = any_schema([{'pattern': rej_regexp.pattern} for rej_regexp in self.reject_regexps])
        return schema


class IntervalValidator(RegexpMatchValidator):
    name = 'interval'

    def __init__(self, parent=None, **kwargs):
        RegexpMatchValidator.__init__(self, parent, **kwargs)
        self.accept(r'^\d+ (second|minute|hour|day|week)s?$')
        self.message = "should be in format 'x (seconds|minutes|hours|days|weeks)'"


class FileValidator(TextValidator):
    name = 'file'

    def validate(self, data):
        import os

        if not os.path.isfile(os.path.expanduser(data)):
            self.errors.add('File %s does not exist' % data)
            return False
        return True

    def schema(self):
        # TODO: file format validator
        return {'type': 'string', 'format': 'file'}


class PathValidator(TextValidator):
    name = 'path'

    def __init__(self, parent=None, allow_replacement=False, allow_missing=False, **kwargs):
        self.allow_replacement = allow_replacement
        self.allow_missing = allow_missing
        Validator.__init__(self, parent, **kwargs)

    def validate(self, data):
        import os

        path = data
        if self.allow_replacement:
            # If string replacement is allowed, only validate the part of the
            # path before the first identifier to be replaced
            pat = re.compile(r'{[{%].*[}%]}')
            result = pat.search(data)
            if not result:
                # Check for old style string replacement if no jinja identifiers are found
                pat = re.compile(r'''
                    %                     # Start with percent,
                    (?:\( ([^()]*) \))    # name in parens (do not capture parens),
                    [-+ #0]*              # zero or more flags
                    (?:\*|[0-9]*)         # optional minimum field width
                    (?:\.(?:\*|[0-9]*))?  # optional dot and length modifier
                    [EGXcdefgiorsux%]     # type code (or [formatted] percent character)
                    ''', re.VERBOSE)

                result = pat.search(data)
            if result:
                path = os.path.dirname(data[0:result.start()])

        if not self.allow_missing and not os.path.isdir(os.path.expanduser(path)):
            self.errors.add('Path %s does not exist' % path)
            return False
        return True

    def schema(self):
        # TODO: Make path format validator
        return {'type': 'string', 'format': 'path'}


class UrlValidator(TextValidator):
    name = 'url'

    def __init__(self, parent=None, protocols=None, **kwargs):
        if protocols:
            self.protocols = protocols
        else:
            self.protocols = ['ftp', 'http', 'https', 'file']
        Validator.__init__(self, parent, **kwargs)

    def validate(self, data):
        regexp = '(' + '|'.join(self.protocols) + '):\/\/(\w+:{0,1}\w*@)?(\S+)(:[0-9]+)?(\/|\/([\w#!:.?+=&%@!\-\/]))?'
        if not isinstance(data, basestring):
            self.errors.add('expecting text')
            return False
        valid = re.match(regexp, data) is not None
        if not valid:
            self.errors.add('value %s is not a valid url' % data)
        return valid

    def schema(self):
        return {'type': 'string', 'format': 'uri'}


class ListValidator(Validator):
    name = 'list'

    def accept(self, value, **kwargs):
        v = self.get_validator(value, **kwargs)
        self.valid.append(v)
        return v

    def validateable(self, data):
        return isinstance(data, list)

    def validate(self, data):
        if not isinstance(data, list):
            self.errors.add('value must be a list')
            return False
        self.errors.path_add_level()
        count = self.errors.count()
        for item in data:
            self.errors.path_update_value('list:%i' % data.index(item))
            self.validate_item(item, self.valid)
        self.errors.path_remove_level()
        return count == self.errors.count()

    def schema(self):
        return {'type': 'array', 'items': any_schema([v.schema() for v in self.valid])}


class DictValidator(Validator):
    name = 'dict'

    def __init__(self, parent=None, **kwargs):
        self.reject = {}
        self.any_key = []
        self.required_keys = []
        self.key_validators = []
        Validator.__init__(self, parent, **kwargs)
        # TODO: not dictionary?
        self.valid = {}

    def accept(self, value, key=None, required=False, **kwargs):
        """
        :param value: validator name, instance or function that returns an instance, which validates the given `key`
        :param string key: The dictionary key to accept
        :param bool required: = Mark this `key` as required
        :raises ValueError: `key` was not specified
        """
        if not key:
            raise ValueError('%s.accept() must specify key' % self.name)

        if required:
            self.require_key(key)

        v = self.get_validator(value, **kwargs)
        self.valid.setdefault(key, []).append(v)
        return v

    def reject_key(self, key, message=None):
        """Rejects a key"""
        self.reject[key] = message

    def reject_keys(self, keys, message=None):
        """Reject list of keys"""
        for key in keys:
            self.reject[key] = message

    def require_key(self, key):
        """Flag key as mandatory"""
        if not key in self.required_keys:
            self.required_keys.append(key)

    def accept_any_key(self, value, **kwargs):
        """Accepts any leftover keys in dictionary, which will be validated with `value`"""
        v = self.get_validator(value, **kwargs)
        self.any_key.append(v)
        return v

    def accept_valid_keys(self, value, key_type=None, key_validator=None, **kwargs):
        """
        Accepts keys that pass a given validator, and validates them using validator specified in `value`

        :param value: Validator name, instance or function returning an instance
            that will be used to validate dict values.
        :param key_type: Name of validator or list of names that determine which keys in this dict `value` will govern
        :param Validator key_validator: A validator instance that will be used to determine which keys in the dict
            `value` will govern
        :raises ValueError: If both `key_type` and `key_validator` are specified.
        """
        if key_type and key_validator:
            raise ValueError('key_type and key_validator are mutually exclusive')
        if key_validator:
            # Make sure errors show up in our list
            key_validator.add_parent(self)
        elif key_type:
            if isinstance(key_type, basestring):
                key_type = [key_type]
            key_validator = self.get_validator('root')
            for key_type in key_type:
                key_validator.accept(key_type)
        else:
            raise ValueError('%s.accept_valid_keys() must specify key_type or key_validator' % self.name)
        v = self.get_validator(value, **kwargs)
        self.key_validators.append((key_validator, v))
        return v

    def validateable(self, data):
        return isinstance(data, dict)

    def validate(self, data):
        if not isinstance(data, dict):
            self.errors.add('value must be a dictionary')
            return False

        count = self.errors.count()
        self.errors.path_add_level()
        for key, value in data.iteritems():
            self.errors.path_update_value('dict:%s' % key)
            # reject keys
            if key in self.reject:
                msg = self.reject[key]
                if msg:
                    from string import Template

                    template = Template(msg)
                    self.errors.add(template.safe_substitute(key=key))
                else:
                    self.errors.add('key \'%s\' is forbidden here' % key)
                continue
                # Get rules for key, most specific rules will be used
            rules = []
            if key in self.valid:
                # Rules for explicitly allowed keys
                rules = self.valid.get(key, [])
            else:
                errors_before_key_val = self.errors.count()
                for key_validator, value_validator in self.key_validators:
                    # Use validate_item to make sure error message is added
                    if key_validator.validateable(key) and key_validator.validate(key):
                        # Rules for a validated_key
                        rules = [value_validator]
                        break
                else:
                    if self.any_key:
                        # Rules for any key
                        rules = self.any_key
                if rules:
                    self.errors.back_out_errors(self.errors.count() - errors_before_key_val)
            if not rules:
                error = 'key \'%s\' is not recognized' % key
                if self.valid:
                    error += ', valid keys: %s' % ', '.join(sorted(self.valid))
                # TODO: print options if accept_valid_keys is used
                self.errors.add(error)
                continue
            self.validate_item(value, rules)
        self.errors.path_remove_level()
        for required in self.required_keys:
            if not required in data:
                self.errors.add('key \'%s\' required' % required)
        return count == self.errors.count()

    def schema(self):
        schema = {'type': 'object'}
        properties = schema['properties'] = {}
        for key, validators in self.valid.iteritems():
            if not validators:
                continue
            properties[key] = any_schema(v for v in validators)
        if self.required_keys:
            schema['required'] = self.required_keys
        if self.any_key:
            schema['additionalProperties'] = any_schema([v.schema() for v in self.any_key])
        else:
            schema['additionalProperties'] = False
        # TODO: implement this, and accept_valid_keys
        #if self.reject_keys:
        #    schema['reject_keys'] = self.reject

        return schema


class QualityValidator(TextValidator):
    name = 'quality'

    def validate(self, data):
        try:
            qualities.get(data)
        except ValueError as e:
            self.errors.add(e.message)
            return False
        return True

    def schema(self):
        # TODO: Implement quality format validator
        return {'type': 'string', 'format': 'quality'}


class QualityRequirementsValidator(TextValidator):
    name = 'quality_requirements'

    def validate(self, data):
        try:
            qualities.Requirements(data)
        except ValueError as e:
            self.errors.add('`%s` is not a valid quality requirement: %s' % (data, e.message))
            return False
        return True

    def schema(self):
        # TODO: Implement qualityRequirements format validator
        return {'type': 'string', 'format': 'qualityRequirements'}


class LazyValidator(object):
    """Acts as a wrapper for a Validator instance, but does not generate the instance until one of its attributes
    needs to be accessed. Used to create validators that may otherwise cause endless loops."""

    def __init__(self, func, parent=None):
        """
        :param func: A function that returns a Validator instance when called.
        :param parent: The parent validator.
        """
        self.func = func
        self.validator = None
        self.parent = parent

    def __getattr__(self, item):
        """Creates the actual validator instance if needed. Return attributes of that instance as our own."""
        if self.validator is None:
            self.validator = self.func()
            assert isinstance(self.validator, Validator)
            self.validator.add_parent(self.parent)
        return getattr(self.validator, item)

    def schema(self):
        """Return the schema of our instance if it has already been created, otherwise return 'ondemand' type."""
        if self.validator is None:
            # TODO: Change this whole class to be a plugin validator implemented with $ref?
            return {}
        else:
            return self.validator.schema()

# ---- TESTING ----


def build_options_validator(options):
    quals = ['720p', '1080p', '720p bluray', 'hdtv']
    options.accept('text', key='path')
    # set
    options.accept('dict', key='set').accept_any_key('any')
    # regexes can be given in as a single string ..
    options.accept('regexp', key='name_regexp')
    options.accept('regexp', key='ep_regexp')
    options.accept('regexp', key='id_regexp')
    # .. or as list containing strings
    options.accept('list', key='name_regexp').accept('regexp')
    options.accept('list', key='ep_regexp').accept('regexp')
    options.accept('list', key='id_regexp').accept('regexp')
    # quality
    options.accept('choice', key='quality').accept_choices(quals, ignore_case=True)
    options.accept('list', key='qualities').accept('choice').accept_choices(quals, ignore_case=True)
    options.accept('boolean', key='upgrade')
    options.accept('choice', key='min_quality').accept_choices(quals, ignore_case=True)
    options.accept('choice', key='max_quality').accept_choices(quals, ignore_case=True)
    # propers
    options.accept('boolean', key='propers')
    message = "should be in format 'x (minutes|hours|days|weeks)' e.g. '5 days'"
    time_regexp = r'\d+ (minutes|hours|days|weeks)'
    options.accept('regexp_match', key='propers', message=message + ' or yes/no').accept(time_regexp)
    # expect flags
    options.accept('choice', key='identified_by').accept_choices(['ep', 'id', 'auto'])
    # timeframe
    options.accept('regexp_match', key='timeframe', message=message).accept(time_regexp)
    # strict naming
    options.accept('boolean', key='exact')
    # watched in SXXEXX form
    watched = options.accept('regexp_match', key='watched')
    watched.accept('(?i)s\d\de\d\d$', message='Must be in SXXEXX format')
    # watched in dict form
    watched = options.accept('dict', key='watched')
    watched.accept('integer', key='season')
    watched.accept('integer', key='episode')
    # from group
    options.accept('text', key='from_group')
    options.accept('list', key='from_group').accept('text')
    # parse only
    options.accept('boolean', key='parse_only')


def complex_test():

    def build_list(series):
        """Build series list to series."""
        series.accept('text')
        series.accept('number')
        bundle = series.accept('dict')
        # prevent invalid indentation level
        """
        bundle.reject_keys(['set', 'path', 'timeframe', 'name_regexp',
            'ep_regexp', 'id_regexp', 'watched', 'quality', 'min_quality',
            'max_quality', 'qualities', 'exact', 'from_group'],
            'Option \'$key\' has invalid indentation level. It needs 2 more spaces.')
        """
        bundle.accept_any_key('path')
        options = bundle.accept_any_key('dict')
        build_options_validator(options)

    root = factory()

    # simple format:
    #   - series
    #   - another series

    simple = root.accept('list')
    build_list(simple)

    # advanced format:
    #   settings:
    #     group: {...}
    #   group:
    #     {...}

    """
    advanced = root.accept('dict')
    settings = advanced.accept('dict', key='settings')
    settings_group = settings.accept_any_key('dict')
    build_options_validator(settings_group)

    group = advanced.accept_any_key('list')
    build_list(group)
    """

    return root


if __name__ == '__main__':
    from flexget.plugins.input.rss import InputRSS
    #v = complex_test()
    v = InputRSS().validator()
    schema = v.schema()

    import json

    print json.dumps(schema, sort_keys=True, indent=4)

    """
    root = factory()
    list = root.accept('list')
    list.accept('text')
    list.accept('regexp')
    list.accept('choice').accept_choices(['foo', 'bar'])

    print root.schema()
    """
