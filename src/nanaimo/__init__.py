#
# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# This software is distributed under the terms of the MIT License.
#
#                                       (@@@@%%%%%%%%%&@@&.
#                              /%&&%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%&@@(
#                              *@&%%%%%%%%%&&%%%%%%%%%%%%%%%%%%&&&%%%%%%%
#                               @   @@@(@@@@%%%%%%%%%%%%%%%%&@@&* @@@   .
#                               ,   .        .  .@@@&                   /
#                                .       .                              *
#                               @@              .                       @
#                              @&&&&&&@. .    .                     *@%&@
#                              &&&&&&&&&&&&&&&&@@        *@@############@
#                     *&/ @@ #&&&&&&&&&&&&&&&&&&&&@  ###################*
#                              @&&&&&&&&&&&&&&&&&&##################@
#                                 %@&&&&&&&&&&&&&&################@
#                                        @&&&&&&&&&&%#######&@%
#  nanaimo                                   (@&&&&####@@*
#
"""
Almost everything in Nanaimo is a :class:`Fixture`. Fixtures can be pytest fixtures, instrument
abstractions, aggregates of other fixtures, or anything else that makes sense. The important thing
is that any fixture can be a pytest fixture or can be awaited directly using :ref:`nait`.

"""
import abc
import asyncio
import logging
import math
import typing

import pluggy


class AssertionError(RuntimeError):
    """
    Thrown by Nanaimo tests when an assertion has failed.

    .. Note::
        This exception should be used only when the state of a :class:`Fixture`
        was invalid. You should use pytest tests and assertions when writing validation
        cases for fixture output like log files or sensor data.
    """
    pass


class Arguments(metaclass=abc.ABCMeta):
    """
    Protocol for argument type that supports both argparse and pytest arguments concepts.

    .. Note::
        This will go away at some disant point in the future where Nanaimo supports only
        Python 3.8 and newer since `PEP-544 protocols <https://www.python.org/dev/peps/pep-0544/>`_
        can be used.
    """
    @abc.abstractmethod
    def add_argument(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        ...

    @abc.abstractmethod
    def set_defaults(self, **kwargs: typing.Any) -> None:
        ...


class Namespace:
    """
    Generic object that acts like :class:`argparse.Namespace` but can be created using pytest
    plugin arguments as well.
    """

    def __init__(self, parent: typing.Optional[typing.Any] = None):
        self._parent = parent

    def __getattr__(self, key: str) -> typing.Any:
        try:
            return self.__dict__[key]
        except KeyError:
            if self._parent is None:
                raise
        return getattr(self._parent, key)

    def __contains__(self, key: str) -> typing.Any:
        if key in self.__dict__:
            return True
        if self._parent is None:
            return False
        else:
            return key in self._parent


class Artifacts(Namespace):
    """
    Namespace returned by :class:`Fixture` objects when invoked that contains the artifacts collected
    from the fixture's activities.

    :param result_code: The value to report as the status of the activity that gathered the artifacts.
    :param parent: A parent namespace to lookup in if the current namespace doesn't have a value.
    """

    def __init__(self, result_code: int = 0, parent: typing.Optional[typing.Any] = None):
        super().__init__(parent)
        self._result_code = result_code

    @property
    def result_code(self) -> int:
        """
        The overall status of the fixture activities. 0 is successful all other values are
        errors.
        """
        return self._result_code

    @result_code.setter
    def result_code(self, new_result: int) -> None:
        self._result_code = new_result

    def __int__(self) -> int:
        return self._result_code


class Fixture(metaclass=abc.ABCMeta):
    """
    Common, abstract class for pytest fixtures based on Nanaimo. Nanaimo fixtures provide a visitor pattern for arguments that
    are common for both pytest extra arguments and for argparse commandline arguments. This allows a Nanaimo fixture to expose
    a direct invocation mode for debuging with the same arguments used by the fixture as a pytest plugin. Additionally all
    Nanaimo fixtures provide a :func:`gather` function that takes a :class:`Namespace` containing the provided arguments and
    returns a set of :class:`Artifacts` gathered by the fixture. The contents of these artifacts are documented by each
    concrete fixture.

    .. invisible-code-block: python
        import nanaimo
        import asyncio

        _doc_loop = asyncio.new_event_loop()

    .. code-block:: python

        class MyFixture(nanaimo.Fixture):
            @classmethod
            def on_visit_test_arguments(cls, arguments: nanaimo.Arguments) -> None:
                arguments.add_argument('--foo', default='bar')

            async def gather(self, args: nanaimo.Namespace) -> nanaimo.Artifacts:
                artifacts = nanaimo.Artifacts(-1)
                # do something and then return
                artifacts.result_code = 0
                return artifacts

    .. invisible-code-block: python
        foo = MyFixture(None)
        ns = nanaimo.Namespace()

        _doc_loop.run_until_complete(foo.gather(ns))

    `MyFixture` can now be used from a commandline like::

        python -m nanaimo MyFixture --foo baz

    or as part of a pytest::

        pytest --foo=baz

    """

    @classmethod
    def get_canonical_name(cls) -> str:
        """
        The name to use as a key for this :class:`Fixture` type.
        If a class defines a string `fixture_name` this will be used
        as the canonical name otherwise it will be the name of the
        fixture class itself.

        .. invisible-code-block: python
            import nanaimo

        .. code-block:: python

            class MyFixture(nanaimo.Fixture):

                fixture_name = 'my_fixture'

            assert 'my_fixture' == MyFixture.get_canonical_name()

        """
        return str(getattr(cls, 'fixture_name', '.'.join([cls.__module__, cls.__qualname__])))

    def __init__(self, manager: 'FixtureManager', loop: typing.Optional[asyncio.AbstractEventLoop] = None):
        self._manager = manager
        self._name = self.get_canonical_name()
        self._logger = logging.getLogger(self._name)
        self._loop = loop

    # +-----------------------------------------------------------------------+
    # | PROPERTIES
    # +-----------------------------------------------------------------------+
    @property
    def name(self) -> str:
        """
        The canonical name for the Fixture.
        """
        return self._name

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """
        The running asyncio EventLoop in use by this Fixture.
        This will be the loop provided to the fixture in the constructor if that loop is still
        running otherwise the loop will be a running loop retrieved by :func:`asyncio.get_event_loop`.
        :raises RuntimeError: if no running event loop could be found.
        """
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.get_event_loop()
        if not self._loop.is_running():
            raise RuntimeError('No running event loop was found!')
        return self._loop

    @property
    def manager(self) -> 'FixtureManager':
        """
        The :class:`FixtureManager` that owns this :class:`Fixture`.
        """
        return self._manager

    @property
    def logger(self) -> logging.Logger:
        """
        A logger for this :class:`Fixture` instance.
        """
        return self._logger

    # +-----------------------------------------------------------------------+
    # | ABSTRACT METHODS
    # +-----------------------------------------------------------------------+
    @classmethod
    @abc.abstractmethod
    def on_visit_test_arguments(cls, arguments: Arguments) -> None:
        """
        Called by the environment before instantiating any :class:`Fixture` instances to register
        arguments supported by each type. These arguments should be portable between both :mod:`argparse`
        and :mod:`pytest`. The fixture is registered for this callback by returning a reference to its
        type from a :attr:`Fixture.Manager.type_factory` annotated function registered as an entrypoint in the Python application.
        """
        ...

    @abc.abstractmethod
    async def gather(self, args: Namespace) -> Artifacts:
        """
        Coroutine awaited to gather fixture artifacts. The fixture should always retrieve new artifacts when invoked
        leaving caching to the caller.
        :param args: The arguments provided for the fixture instance.
        :type args: Namespace
        :return: A set of artifacts with the :attr:`Artifacts.result_code` set to indicate the success or failure of the fixture's artifact gathering activies.
        """
        ...

    # +-----------------------------------------------------------------------+
    # | ASYNC HELPERS
    # +-----------------------------------------------------------------------+

    async def countdown_sleep(self, sleep_time_seconds: float) -> None:
        """
        Calls :func:`asyncio.sleep` for 1 second then emits an :meth:`logging.Logger.info`
        of the time remaining until `sleep_time_seconds`.
        This is useful for long waits as an indication that the process is not deadlocked.

        :param sleep_time_seconds:  The amount of time in seconds for this coroutine to wait
            before exiting. For each second that passes while waiting for this amount of time
            the coroutine will :func:`asyncio.sleep` for 1 second.
        :type sleep_time_seconds: float
        """
        count_down = sleep_time_seconds
        while count_down >= 0:
            self._logger.info('%d', math.ceil(count_down))
            await asyncio.sleep(1)
            count_down -= 1

    async def observe_tasks_assert_not_done(self,
                                            observer_co_or_f: typing.Union[typing.Coroutine, asyncio.Future],
                                            timeout_seconds: float,
                                            *args: typing.Union[typing.Coroutine, asyncio.Future]) -> typing.Set[asyncio.Future]:
        """
        Allows running a set of tasks but returning when an observer task completes. This allows a pattern where
        a single task is evaluating the side-effects of other tasks as a gate to continuing the test.
        """
        done, pending = await self._observe_tasks(observer_co_or_f, timeout_seconds, *args)
        if len(done) > 1:
            raise AssertionError('Tasks under observation completed before the observation was complete.')
        return pending

    async def observe_tasks(self,
                            observer_co_or_f: typing.Union[typing.Coroutine, asyncio.Future],
                            timeout_seconds: float,
                            *args: typing.Union[typing.Coroutine, asyncio.Future]) -> typing.Set[asyncio.Future]:
        """
        Allows running a set of tasks but returning when an observer task completes. This allows a pattern where
        a single task is evaluating the side-effects of other tasks as a gate to continuing the test.
        """

        done, pending = await self._observe_tasks(observer_co_or_f, timeout_seconds, *args)
        return pending

    # +-----------------------------------------------------------------------+
    # | PRIVATE
    # +-----------------------------------------------------------------------+

    async def _observe_tasks(self,
                             observer_co_or_f: typing.Union[typing.Coroutine, asyncio.Future],
                             timeout_seconds: float,
                             *args: typing.Union[typing.Coroutine, asyncio.Future]) -> \
            typing.Tuple[typing.Set[asyncio.Future], typing.Set[asyncio.Future]]:

        observing_my_future = asyncio.ensure_future(observer_co_or_f)
        the_children_are_our_futures = [observing_my_future]
        for co_or_f in args:
            the_children_are_our_futures.append(asyncio.ensure_future(co_or_f))

        start_time = self.loop.time()
        wait_timeout = (timeout_seconds if timeout_seconds > 0 else None)

        while True:
            done, pending = await asyncio.wait(
                the_children_are_our_futures,
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED)

            if observing_my_future.done():
                return done, pending

            if wait_timeout is not None and self.loop.time() - start_time > wait_timeout:
                break

        raise asyncio.TimeoutError()


class FixtureManager:
    """
    Object that scopes a set of :class:`Fixture`. Fixture managers provide a common context for fixtures
    across pytest and command-line environments.
    """

    plugin_name = 'nanaimo'

    type_factory_spec = pluggy.HookspecMarker(plugin_name)
    type_factory = pluggy.HookimplMarker(plugin_name)

    def __init__(self) -> None:
        self._pluginmanager = pluggy.PluginManager(self.plugin_name)
        self._fixture_cache = dict()  # type: typing.Dict[str, Fixture]

        class PluginNamespace:
            @self.type_factory_spec
            def get_fixture_type(self) -> typing.Type['Fixture']:
                raise NotImplementedError()

        self._pluginmanager.add_hookspecs(PluginNamespace)
        self._pluginmanager.load_setuptools_entrypoints(self.plugin_name)

    def fixture_types(self) -> typing.Generator:
        """
        Yields each fixture type registered with this object. The types may or may not
        have already been instantiated.
        """
        for fixture_type in self._pluginmanager.hook.get_fixture_type():
            yield fixture_type

    def get_fixture(self, fixture_name: str) -> Fixture:
        """
        Get a fixture instance creating it if it wasn't already.

        :param fixture_name: The canonical name of the fixture.
        :type fixture_name: str
        :param loop:
        :return: A manager-scoped fixture instance (i.e. One-and-only-one
            fixture instance with this name for this manager object).
        """
        try:
            return self._fixture_cache[fixture_name]
        except KeyError:
            for fixture_type in self._pluginmanager.hook.get_fixture_type():
                if fixture_type.get_canonical_name() == fixture_name:
                    fixture = typing.cast(Fixture, fixture_type(self))
                    self._fixture_cache[fixture_name] = fixture
                    return fixture
            raise
