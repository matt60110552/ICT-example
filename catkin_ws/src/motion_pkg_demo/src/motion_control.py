#!/usr/bin/env python

# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Grasping example with the PyRobot API.
Follow the associated README for installation instructions.
"""

import argparse
import copy
import signal
import sys
import time

import numpy as np
import rospy
from geometry_msgs.msg import Quaternion, PointStamped
from pyrobot import Robot
from tf import TransformListener
from motion_pkg_demo.srv import Motion,MotionResponse
from motion_pkg_demo.srv import Grasp_Point, Grasp_PointResponse
from baseline_navi_demo.srv import StageChange
from baseline_navi_demo.srv import Stage_Grasp, Stage_GraspResponse

from baseline_navi_demo.msg import TaskStage
from std_msgs.msg import Int32, Bool
from navigation_controller.srv import command

BB_SIZE = 5
MAX_DEPTH = 3.0
BASE_FRAME = "base_link"
KINECT_FRAME = "camera_color_optical_frame"
DEFAULT_PITCH = 1.57
MIN_DEPTH = 0.1
N_SAMPLES = 100
PATCH_SIZE = 100

from IPython import embed

class Motion_(object):
    """
    This class contains functionality to make the LoCoBot grasp objects placed in front of it.
    """

    def __init__(self):
                # TODO - use planning_mode=no_plan, its better
        # self.robot = Robot(
        #     "locobot_kobuki",
        #     arm_config={"use_moveit": True, "moveit_planner": "ESTkConfigDefault"},
        # )
        self.robot = Robot(
            "locobot",
            arm_config={"use_moveit": True, "moveit_planner": "ESTkConfigDefault"},
        )
        self.pregrasp_height = 0.17
        self.grasp_height = 0.11
        self.default_Q = Quaternion(0.0, 0.0, 0.0, 1.0)
        self.grasp_Q = Quaternion(0.0, 0.707, 0.0, 0.707)
        self.retract_position = list([-1.5, 0.5, 0.3, -0.7, 0.0])
        self.reset_pan = 0.0
        self.reset_tilt = 0.8
        self.n_tries = 5
        self._sleep_time = 2.5
        self._transform_listener = TransformListener()
        # self.motion_srv = rospy.Service('locobot_motion', Motion, self.handle_grasp)
        rospy.wait_for_service('locobot_grasppoint')
        self.grasppoint_service = rospy.ServiceProxy('locobot_grasppoint', Grasp_Point)
        # self.grasp_pub = rospy.Publisher("locobot_motion/grasp_start", Int32, queue_size = 1)
        # self.stage_sub = rospy.Subscriber('locobot_motion/demo_initial', Bool, self.demo_cb, queue_size = 1)
        self.grasp_stage_srv = rospy.Service('baseline_navi_demo/stage_grasp', Stage_Grasp, self.grasp_stage_service_cb)
        rospy.wait_for_service('pos_cmd')
        self.goal_service = rospy.ServiceProxy('pos_cmd', command)
        self.color = ""

    def loop_service_confirm(self):
        goal_achived = False
        while not goal_achived:
            try:
                resp = self.goal_service(0, 0, 0, 0)
                goal_achived = resp.run_completed
            except rospy.ServiceException, e:
                print("Service call failed: %s", e)

        return goal_achived

    def pos_cmd_request(self, request_type, request_x, request_y, request_theta):
        try:
            resp = self.goal_service(request_type, request_x, request_y, request_theta)
        except rospy.ServiceException, e:
            print("Service call failed: %s", e)

    def send_goal(self, goal_x, goal_y, goal_theta):
        self.pos_cmd_request(2, goal_x, goal_y, 0)
        goal_achived = self.loop_service_confirm()

        if not goal_achived:
            print("Fail at type 2 VSLAM Navigation")
            return False

        self.pos_cmd_request(1, 0, 0, goal_theta)
        goal_achive = self.loop_service_confirm()

        return goal_achived

    # def demo_cb(self, demo_msg):
    #     if demo_msg.data == False:
    #         return

    #     #start demo grasping
    #     self.send_goal(1.1, 0.45, 1.57)
    #     self.robot.camera.set_tilt(self.reset_tilt)
    #     self.grasp_pub.publish(1)

    def reset(self):
        """ 
        Resets the arm to it's retract position.
        :returns: Success of the reset procedure
        :rtype: bool
        """
        success = False
        for _ in range(self.n_tries):
            success = self.robot.arm.set_joint_positions(self.retract_position)
            if success == True:
                break
        self.robot.gripper.open()
        self.robot.camera.set_pan(self.reset_pan)
        self.robot.camera.set_tilt(self.reset_tilt)
        return success

    def grasp(self, grasp_pose):
        """ 
        Performs manipulation operations to grasp at the desired pose.
        
        :param grasp_pose: Desired grasp pose for grasping.
        :type grasp_pose: list
        :returns: Success of grasping procedure.
        :rtype: bool
        """

        pregrasp_position = [grasp_pose[0], grasp_pose[1], self.pregrasp_height]
        # pregrasp_pose = Pose(Point(*pregrasp_position), self.default_Q)
        grasp_angle = self.get_grasp_angle(grasp_pose)
        grasp_position = [grasp_pose[0], grasp_pose[1], self.grasp_height]
        # grasp_pose = Pose(Point(*grasp_position), self.grasp_Q)

        rospy.loginfo("Going to pre-grasp pose:\n\n {} \n".format(pregrasp_position))
        result = self.set_pose(pregrasp_position, roll=grasp_angle)
        # if not result:
        #     print("weird")
        #     return False
        time.sleep(self._sleep_time)

        rospy.loginfo("Going to grasp pose:\n\n {} \n".format(grasp_position))
        result = self.set_pose(grasp_position, roll=grasp_angle)
        # if not result:
        #     return False
        time.sleep(self._sleep_time)

        rospy.loginfo("Closing gripper")
        self.robot.gripper.close()
        # time.sleep(self._sleep_time)
        time.sleep(1)

        rospy.loginfo("Going to pre-grasp pose")
        result = self.set_pose(pregrasp_position, roll=grasp_angle)
        if not result:
            return False
        # time.sleep(self._sleep_time)
        time.sleep(1)
        print("end of grasp func")

    def set_pose(self, position, pitch=DEFAULT_PITCH, roll=0.0):
        """ 
        Sets desired end-effector pose.
        
        :param position: End-effector position to reach.
        :param pitch: Pitch angle of the end-effector.
        :param roll: Roll angle of the end-effector
        :type position: list
        :type pitch: float
        :type roll: float
        :returns: Success of pose setting process.
        :rtype: bool
        """

        success = 0
        for _ in range(self.n_tries):
            position = np.array(position)
            success = self.robot.arm.set_ee_pose_pitch_roll(
                position=position, pitch=pitch, roll=roll, plan=False, numerical=False
            )
            if success == 1:
                print("set_pose success")
                break
        return success

    def get_grasp_angle(self, grasp_pose):
        """ 
        Obtain normalized grasp angle from the grasp pose.
        This is needs since the grasp angle is relative to the end effector.
        
        :param grasp_pose: Desired grasp pose for grasping.
        :type grasp_pose: list
        :returns: Relative grasp angle
        :rtype: float
        """

        cur_angle = np.arctan2(grasp_pose[1], grasp_pose[0])
        delta_angle = grasp_pose[2] + cur_angle
        if delta_angle > np.pi / 2:
            delta_angle = delta_angle - np.pi
        elif delta_angle < -np.pi / 2:
            delta_angle = 2 * np.pi + delta_angle
        return delta_angle

    def exit(self):
        """
        Graceful exit.
        """

        rospy.loginfo("Exiting...")
        self.reset()
        sys.exit(0)

    def signal_handler(self, sig, frame):
        """
        Signal handling function.
        """
        self.exit()

    def handle_grasp(self, req):
        rospy.loginfo("Grasp attempt x={:.4f},y={:.4f},theta={:.4f}".format(req.x,req.y,req.theta))
        self.color = req.color
        try:
            success = self.reset()
            assert  success
        except:
            rospy.logerr("Arm reset failed")
        grasp_pose = [req.x,req.y,req.theta]
        #grasp_pose = motion.compute_grasp(display_grasp=False)
        print("\n\n Grasp Pose: \n\n {} \n\n".format(grasp_pose))
        self.robot.camera.set_tilt(0.0)
        self.grasp(grasp_pose)

        #robotics arm placing
        rospy.loginfo('Going to placing pose')
        print(self.color)
        if self.color == 'red':
            # result = self.set_pose([0.234, -0.335, 0.3], roll=0.0)
            result = self.set_pose([0.234, -0.235, 0.3], roll=0.0)
            print("red")
        elif self.color == 'green':
            # result = self.set_pose([0.14, -0.335, 0.3], roll=0.0)
            result = self.set_pose([0.174, -0.235, 0.3], roll=0.0)
            print("green")
        elif self.color == 'blue':
            # result = self.set_pose([0.024, -0.335, 0.3], roll=0.0)
            result = self.set_pose([0.114, -0.235, 0.3], roll=0.0)
            print("blue")
        if not result:
            print("False")
            return False
        time.sleep(self._sleep_time)
        # rospy.loginfo('Opening gripper')
        # self.robot.gripper.open()
        # rospy.loginfo('Going to placing above pose')
        # result = self.set_pose([0.124, -0.335, 0.25], roll=0.0)
        # if not result:
        #     return False
        # self.set_pose([0.15,0.0,0.3],roll=0.0)
        # self.pos_cmd_request(1, 0, 0, 3.14)
        # goal_achived = self.loop_service_confirm()
        # print("finished pos_cmd_request: [0.124, -0.335, 0.25], roll=0.0")
        # self.send_goal(0, 0, 0)
        # print("finished send_goal: (0, 0, 0)")
        # self.reset()

        return MotionResponse("ok")

    def grasp_stage_service_cb(self, grasp_request):
        if grasp_request.request == 1:
            self.reset()
            print("call grasp.py")
            response = self.grasppoint_service(True)
            print("get point")
            self.handle_grasp(response)
            # pred_grasp = self.compute_grasp()
            # print("Pred grasp: {}".format(pred_grasp))
            # try:
            #     resp = self.motion_service(pred_grasp[0], pred_grasp[1], pred_grasp[2], self.color)
            # except rospy.ServiceException, e:
            #     print("Service call failed: %s", e)
            # print("finished grasp_stage_service_cb")
            return Stage_GraspResponse(True)
        elif grasp_request.request == 3:
            rospy.loginfo('Opening gripper')
            self.robot.gripper.open()
            rospy.loginfo('Going to placing above pose')
            self.set_pose([0.15,0.0,0.3],roll=0.0)
            rospy.loginfo('turn around')
            self.send_goal(0, 0, -3.14)
            rospy.loginfo('back to original point')
            self.send_goal(0, 0, 0)
            return Stage_GraspResponse(True)
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process args for grasper")
    parser.add_argument('--n_grasps', help='Number of grasps for inference', type=int, default=5)
    parser.add_argument('--n_samples', help='Number of samples for a single grasp inference', type=int, default=N_SAMPLES)
    parser.add_argument('--patch_size', help='Size of a sampled grasp patch', type=int, default=PATCH_SIZE)
    parser.add_argument('--no_visualize', help='False to visualize grasp at each iteration, True otherwise',
                        dest='display_grasp', action='store_false')
    parser.set_defaults(no_visualize=True)

    # args = parser.parse_args()
    args, unknown = parser.parse_known_args()
    rospy.init_node('locobot_motion_server')
    motion = Motion_()
    # motion.set_pose([0.15,0.0,0.3],roll=0.0)
    
    # motion.reset()
    # motion.robot.camera.set_tilt(0.0)
    rospy.spin()