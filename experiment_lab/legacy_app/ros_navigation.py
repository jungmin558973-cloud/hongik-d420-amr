"""Nav2 actions and latest command input. The existing Lab owns motor publishing."""
import asyncio
import math
import threading
import time
import uuid


async def result(future,timeout=15.):
    end=time.monotonic()+timeout
    while not future.done():
        if time.monotonic()>end:raise ValueError('내비게이션 응답 시간이 초과됐습니다.')
        await asyncio.sleep(.02)
    return future.result()


class RosNavigation:
    def __init__(self,node,frame_id='map'):
        from rclpy.action import ActionClient
        from rclpy.qos import QoSProfile,ReliabilityPolicy
        from nav2_msgs.action import ComputePathToPose,NavigateToPose
        from nav2_msgs.srv import IsPathValid
        from rcl_interfaces.srv import SetParameters
        from geometry_msgs.msg import Twist
        if frame_id not in ('map','odom'):raise ValueError('지원하지 않는 주행 좌표계')
        self.frame_id=frame_id
        self.node=node;self.lock=threading.RLock()
        self.planner=ActionClient(node,ComputePathToPose,'/d420_nav/compute_path_to_pose')
        self.navigator=ActionClient(node,NavigateToPose,'/d420_nav/navigate_to_pose')
        self.validator=node.create_client(IsPathValid,'/d420_nav/is_path_valid')
        self.parameters=node.create_client(SetParameters,'/d420_nav/controller_server/set_parameters')
        self.subscription=node.create_subscription(Twist,'/d420/nav_preview_cmd_vel',self.receive,
            QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
        self.handle=None;self.phase='idle';self.goal_id=None
        self.command=(0.,0.);self.command_at=0.;self.message='';self.remaining=None

    def ready(self):return self.planner.server_is_ready() and self.navigator.server_is_ready() and self.validator.service_is_ready() and self.parameters.service_is_ready()

    async def set_speed(self,speed):
        from navigation import finite
        from rcl_interfaces.msg import Parameter,ParameterValue,ParameterType
        from rcl_interfaces.srv import SetParameters
        if not finite(speed) or not .03<=speed<=1.5:raise ValueError('이동 속도는 3~150cm/s 범위입니다.')
        request=SetParameters.Request()
        request.parameters=[Parameter(name='FollowPath.desired_linear_vel',value=ParameterValue(
            type=ParameterType.PARAMETER_DOUBLE,double_value=float(speed)))]
        response=await result(self.parameters.call_async(request),5)
        if len(response.results)!=1 or not response.results[0].successful:
            raise ValueError('Nav2에 선택 속도를 적용하지 못했습니다.')

    def pose(self,point):
        from geometry_msgs.msg import PoseStamped
        msg=PoseStamped();msg.header.frame_id=self.frame_id;msg.header.stamp=self.node.get_clock().now().to_msg()
        msg.pose.position.x=float(point['x']);msg.pose.position.y=float(point['y'])
        msg.pose.orientation.z=math.sin(point['yaw']/2);msg.pose.orientation.w=math.cos(point['yaw']/2)
        return msg

    async def plan(self,point):
        from nav2_msgs.action import ComputePathToPose
        from nav2_msgs.srv import IsPathValid
        if not self.ready():raise ValueError('먼저 지점 이동 준비를 눌러 주세요.')
        request=ComputePathToPose.Goal();request.goal=self.pose(point);request.planner_id='GridBased'
        handle=await result(self.planner.send_goal_async(request))
        if not handle.accepted:raise ValueError('경로 요청이 거절됐습니다.')
        response=await result(handle.get_result_async())
        path=response.result.path
        if response.status!=4 or not path.poses:raise ValueError('이 지점까지 이동할 경로가 없습니다.')
        last=path.poses[-1].pose.position
        if math.hypot(last.x-point['x'],last.y-point['y'])>.08:
            raise ValueError('경로 끝점이 선택한 지점과 다릅니다.')
        check=IsPathValid.Request();check.path=path
        checked=await result(self.validator.call_async(check),5)
        if not checked.is_valid:raise ValueError('차체가 통과할 수 없는 경로입니다.')
        return [[p.pose.position.x,p.pose.position.y] for p in path.poses]

    async def start(self,point):
        from nav2_msgs.action import NavigateToPose
        with self.lock:
            if self.phase in ('moving','canceling','starting'):raise ValueError('이전 지점 이동이 끝날 때까지 기다려 주세요.')
            self.phase='starting';self.goal_id=uuid.uuid4().hex
            self.command=(0.,0.);self.command_at=0.;self.message=''
            key=self.goal_id
        request=NavigateToPose.Goal();request.pose=self.pose(point)
        try:
            handle=await result(self.navigator.send_goal_async(request,feedback_callback=self.feedback),5)
        except Exception:
            self.phase='failed';raise
        if not handle.accepted:self.phase='failed';raise ValueError('지점 이동 요청이 거절됐습니다.')
        with self.lock:
            self.handle=handle;self.phase='moving';self.command_at=0.
        handle.get_result_async().add_done_callback(lambda future:self.completed(future,key))
        return key

    def receive(self,msg):
        with self.lock:
            if self.phase=='moving':
                self.command=(msg.linear.x,msg.angular.z);self.command_at=time.monotonic()

    def feedback(self,msg):
        with self.lock:self.remaining=msg.feedback.distance_remaining

    def completed(self,future,key):
        with self.lock:
            if self.goal_id!=key:return
            try:self.phase={4:'succeeded',5:'canceled'}.get(future.result().status,'failed')
            except Exception:self.phase='failed'
            self.command=(0.,0.);self.command_at=0.;self.handle=None

    def cancel(self):
        with self.lock:
            if self.handle is not None and self.phase=='moving':
                self.phase='canceling';self.command=(0.,0.);self.command_at=0.
                self.handle.cancel_goal_async()

    def snapshot(self):
        duplicate=self.node.count_publishers('/d420/nav_preview_cmd_vel')>1
        with self.lock:return {'ready':self.ready() and not duplicate,'goal_id':self.goal_id,
            'phase':'failed' if duplicate else self.phase,
            'command':self.command,'command_at':self.command_at,'remaining':self.remaining,
            'message':'내비게이션 명령 발행자가 중복됐습니다.' if duplicate else self.message}
