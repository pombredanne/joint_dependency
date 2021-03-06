from __future__ import division
import numpy as np
import pandas as pd
import random
from enum import Enum
from joint_dependency.recorder import Record


def get_state(q, states):
    i = 0
    for i, k in enumerate(states):
        if q < k:
            return i
    return i + 1


def sgn(x):
    if x < 0:
        return -1
    else:
        return 1


class Joint(object):
    def __init__(self, states, dampings, limits, noise, index=None):
        if index is not None:
            self.index = index
        self.max_vel = np.inf
        self.vel = 0
        self.q = 0
        self.states = states
        self.dampings = dampings
        self.min_limit = limits[0]
        self.max_limit = limits[1]
        self.direction = 1
        self.locked = False
        self.noise = noise

    def add_force(self, f):
        if not self.locked:
            state = get_state(self.q, self.states)
            self.vel += f
            self.vel = min(self.vel, self.max_vel)

    def lock(self):
        self.vel = 0
        self.locked = True

    def unlock(self):
        self.locked = False

    def is_locked(self):
        return self.locked

    @Record(["q", "v", "locked", "direction"])
    def step(self, dt):
        if self.locked:
            return [self.q, self.vel, self.locked, 0]

        self.q += self.vel * dt
        vel = self.vel * dt

        change_direction = -1
        if (self.max_limit is not None) and (self.q > self.max_limit):
            self.q = self.max_limit
            change_direction = 1
        if (self.min_limit is not None) and (self.q < self.min_limit):
            self.q = self.min_limit
            change_direction = 1

        state = get_state(self.q, self.states)
        damping = self.dampings[state]
        direction = -change_direction * sgn(self.vel)
        tmp_sqr_vel = max(self.vel ** 2 - abs(damping * self.vel * dt), 0)

        self.vel = direction * np.sqrt(tmp_sqr_vel)

        return [self.q, self.vel, self.locked, direction]

    def get_vel(self):
        return random.gauss(self.vel, self.noise['vel'])

    def get_q(self):
        return random.gauss(self.q, self.noise['q'])


class World(object):
    def __init__(self, joints):
        self.joints = joints
        self.listeners = []
        self.time = 0
        for joint in joints:
            joint.world = self

    def step(self, dt):
        self.time += dt
        for joint in self.joints:
            joint.step(dt)
        self._inform_listeners(dt)

    def get_index(self):
        return [pd.to_datetime(self.time, unit="s")]

    def register(self, listener):
        self.listeners.append(listener)

    def get_joint(self, num):
        return self.joints[num]

    def _inform_listeners(self, dt):
        for listener in self.listeners:
            listener.step(dt)

    def add_joint(self, joint):
        self.joints.append(joint)
        joint.world = self
        joint.index = len(self.joints)-1


class Controller(object):
    def __init__(self, world, joint_idx):
        self.joint = world.joints[joint_idx]
        self.world = world
        self.world.register(self)

        self.index = joint_idx

        self.force_control = False
        self.force_controller = ForceController(world, joint_idx)

        self.position_control = False
        self.position_controller = PositionController(world, joint_idx)

        self.max_force = 15

    @Record(["applied_force", "desired_force"])
    def step(self, dt):
        desired_force = 0
        if self.force_control:
            if self.force_controller.is_done():
                self.force_control = False
            else:
                desired_force = self.force_controller.step(dt)

        elif self.position_control:
            if self.position_controller.is_done():
                self.position_control = False
            else:
                desired_force = self.position_controller.step(dt)

        sign = sgn(desired_force)
        applied_force = sign * min(abs(desired_force), self.max_force)

        self.joint.add_force(applied_force)

        return [applied_force, desired_force]

    def move_to(self, goal):
        self.position_controller.move_to(goal)
        self.position_control = True

    def apply_force(self, time, force):
        self.force_controller.apply_force(time, force)
        self.force_control = True

    def is_done(self):
        return self.position_control is False and self.force_control is False


class PositionController(object):
    def __init__(self, world, joint_idx):
        self.goal_pos = None
        self.joint = world.joints[joint_idx]
        self.q_eps = .5
        self.v_eps = 10e-3
        self.q = 0
        self.v = 0
        self.i = 0
        self.kp = 2
        self.kd = 1
        self.ki = 0

        self.max_force = 30

    def move_to(self, pos):
        self.goal_pos = pos

    def step(self, dt):
        self.q = self.joint.get_q()
        self.v = self.joint.get_vel()

        return self._pid_control()

    def _pid_control(self):
        if self.goal_pos is None:
            return 0
        if self.is_done():
            return 0

        self.i += (self.goal_pos - self.q)
        force = (self.kp * (self.goal_pos - self.q)
                 + self.kd * (-self.v)
                 + self.ki * self.i)
        return force

    def is_done(self):
        if self.goal_pos is None:
            return True
        if (abs(self.q - self.goal_pos) < self.q_eps and
                abs(self.v) < self.v_eps):
            return True
        if self.joint.is_locked():
            return True

        return False


class ForceController(object):
    def __init__(self, world, joint_idx):
        self.joint = world.joints[joint_idx]
        self.time = 0
        self.force = 0

    def apply_force(self, time, force):
        self.force = force
        self.time = time

    def is_done(self):
        return self.time <= 0

    def step(self, dt):
        self.time = max(0, self.time - (dt))
        if self.time > 0:
            return self.force
        else:
            return 0


class Locker(object):
    def __init__(self, world, locker, locked, lower, upper):
        self.world = world
        self.world.register(self)
        self.locker = locker
        self.locked = locked
        self.lower = lower
        self.upper = upper

    def step(self, dt):
        if self.lower < self.locker.q < self.upper:
            if not self.locked.is_locked():
                self.locked.lock()
        else:
            if self.locked.is_locked():
                self.locked.unlock()


class MultiLocker(object):
    def __init__(self, world, locker, locked, locks):
        self.world = world
        self.world.register(self)
        self.locker = locker
        self.locked = locked
        self.locks = locks

    def step(self, dt):
        is_locked = self.locked.is_locked()
        should_be_locked = False
        for lock in self.locks:
            if lock[0] <= self.locker.q <= lock[1]:
                should_be_locked = True

        if is_locked and not should_be_locked:
            # print("unlock")
            self.locked.unlock()
        elif not is_locked and should_be_locked:
            # print("lock")
            self.locked.lock()


class ActionMachine(object):
    def __init__(self, world, controller, tau=0.1):
        self.world = world
        self.controllers = controller
        self.tau = tau

    def run_action(self, pos):
        for j, p in enumerate(pos):
            self.controllers[j].move_to(p)
            while not self.controllers[j].is_done():
                self.world.step(self.tau)

    def check_state(self, joint):
        old_pos = self.world.joints[joint].q
        self.controllers[joint].apply_force(1, 10)
        for i in range(10):
            self.world.step(self.tau)
        new_pos = self.world.joints[joint].q

        if abs(old_pos - new_pos) > 10e-3:
            locked_state = 0
        else:
            locked_state = 1

        return locked_state


class Furniture(Enum):
    drawer_key = 0
    drawer_handle = 1
    cupboard_key = 2
    cupboard_handle = 3
    # window = 4


def create_furniture(furniture, *args, **kwargs):
    if furniture == Furniture.drawer_key:
        return create_drawer_with_key(*args, **kwargs)
    elif furniture == Furniture.drawer_handle:
        return create_drawer_with_handle(*args, **kwargs)
    elif furniture == Furniture.cupboard_key:
        return create_cupboard_with_key(*args, **kwargs)
    elif furniture == Furniture.cupboard_handle:
        return create_cupboard_with_handle(*args, **kwargs)
    # elif furniture == Furniture.window:
    #     return create_window(*args, **kwargs)
    else:
        raise TypeError("{} is not a valid furniture.".format(furniture))


def create_drawer_with_key(world, noise, limits):
    open_at = np.random.randint(limits[0][0]+20, limits[0][1]-20)
    open_d = (open_at - 10, open_at + 10)

    # The 'handle'
    states = [open_d[0], open_d[1]]
    dampings = [15, 200, 15]
    world.add_joint(Joint(states, dampings, limits[0], noise))

    # The 'window'
    states = [limits[1][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[1], noise))

    MultiLocker(world, locker=world.joints[-2], locked=world.joints[-1],
                locks=[(limits[0][0], open_d[0]), (open_d[1], limits[0][1])])


def create_drawer_with_handle(world, noise, limits):
    open_upper = np.random.uniform() > .5
    if open_upper:
        open_d = (limits[0][1] - 20, limits[0][1])
        locked_d = (limits[0][0], limits[0][1]-20)
    else:
        open_d = (limits[0][0], limits[0][0]+20)
        locked_d = (limits[0][0]+20, limits[0][1])

    # The 'handle'
    states = [open_d[0], open_d[1]]
    dampings = [15, 200, 15]
    world.add_joint(Joint(states, dampings, limits[0], noise))

    # The 'window'
    states = [limits[1][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[1], noise))

    MultiLocker(world, locker=world.joints[-2], locked=world.joints[-1],
                locks=[locked_d])


def create_cupboard_with_key(world, noise, limits):
    open_at = np.random.randint(limits[0][0]+20, limits[0][1]-20)
    open_d = (open_at - 10, open_at + 10)

    # The 'handle'
    states = [open_d[0], open_d[1]]
    dampings = [15, 200, 15]
    world.add_joint(Joint(states, dampings, limits[0], noise))

    # The 'window'
    states = [limits[1][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[1], noise))

    MultiLocker(world, locker=world.joints[-2], locked=world.joints[-1],
                locks=[(limits[0][0], open_d[0]), (open_d[1], limits[0][1])])


def create_cupboard_with_handle(world, noise, limits):
    open_upper = np.random.uniform() > .5
    if open_upper:
        open_d = (limits[0][1] - 20, limits[0][1])
        locked_d = (limits[0][0], limits[0][1]-20)
    else:
        open_d = (limits[0][0], limits[0][0]+20)
        locked_d = (limits[0][0]+20, limits[0][1])

    # The 'handle'
    states = [open_d[0], open_d[1]]
    dampings = [15, 200, 15]
    world.add_joint(Joint(states, dampings, limits[0], noise))

    # The 'window'
    states = [limits[1][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[1], noise))

    MultiLocker(world, locker=world.joints[-2], locked=world.joints[-1],
                locks=[locked_d])


def create_window(world, noise, limits):
    tilt_at = (limits[0][0]+limits[0][1])/2
    tilt_d = [(limits[0][0], tilt_at - 10), (limits[0][1], tilt_at + 10)]

    open_upper = np.random.uniform() > .5
    if open_upper:
        open_d = (limits[0][1] - 20, limits[0][1])
        locked_d = (limits[0][0], limits[0][1]-20)
    else:
        open_d = (limits[0][0], limits[0][0]+20)
        locked_d = (limits[0][0]+20, limits[0][1])

    # The 'handle'
    states = [limits[0][0], tilt_d[0][1], tilt_d[1][0], limits[0][1]]
    dampings = [15, 200, 15, 200, 15]
    world.add_joint(Joint(states, dampings, limits[0], noise))

    # The 'tilted window'
    states = [limits[1][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[1], noise))

    # The 'open window'
    states = [limits[2][1]]
    dampings = [15, 15]
    world.add_joint(Joint(states, dampings, limits[2], noise))

    MultiLocker(world, locker=world.joints[-3], locked=world.joints[-2],
                locks=tilt_d)

    MultiLocker(world, locker=world.joints[-3], locked=world.joints[-1],
                locks=locked_d)


def create_world(n=3):
    noise = {'q': 10e-6, 'vel': 10e-6}
    world = World([])
    for _ in range(n):
        next_furniture = random.choice(list(Furniture))
        create_furniture(next_furniture, world, noise, [[0, 180], [0, 120]])

    return world

def create_lockbox(num_of_joints=5, noise=None):
    if noise is None:
        noise = {'q': 10e-6, 'vel': 10e-6}

    world = World([])

    limits = (0, 180)

    for i in range(num_of_joints):
        dampings = [15, 200, 15]

        m = random.randint(10, 170)
        if i > 0:
            locks = [lower, upper]

        lower = (0, m - 10)
        upper = (m + 10, 180)

        world.add_joint(Joint([lower[1], upper[0]], dampings,
                                   limits=limits, noise=noise))
        if i > 0:
            MultiLocker(world, locker=world.joints[i-1],
                        locked=world.joints[i], locks=locks)

        print("Joint {} opens at {} - {}".format(i, lower[1], upper[0]))
    # for i in range(2, 5):
    #     MultiLocker(self.world, locker=self.world.joints[i-1],
    #                 locked=self.world.joints[i], locks=[closed])

    # controllers = [Controller(world, j)
    #                for j, _ in enumerate(world.joints)]
    # action_machine = ActionMachine(world, controllers, tau)

    return world
