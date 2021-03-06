'''
反投影预警类
'''
import cv2
import numpy as np

from radar_class.camera import read_yaml #相机参数
from radar_class.common import plot,is_inside 
from radar_class.config import color2enemy, enemy_case

class Reproject(object):
    '''
    反投影预警
    '''
    _iou_threshold=0.8
    def __init__(self,frame,camera_type,region,real_size,K_0,C_0,touch_api,debug=False):
        self._scene=frame.copy()#初始化的图像
        self._region=region
        self._scene_region=None
        frame_size=read_yaml(camera_type)[4]
        self._size=frame_size# 目前没有用到
        self._K_O=K_0
        self._C_O=C_0
        self._real_size=real_size
        self._cache=None
        self._debug=debug
        self._scene_init=False

    def _plot_regin(self):
        '''
        计算预警区域反投影坐标，用第一帧初始化了就可以了
        return: 预警区域反投影坐标
        '''
        for r in self._region.keys():
            #格式解析
            type,shape_type,team,location,height_type=r.split('_')
            if location not in enemy_case or color2enemy[team]==self._enemy: #筛选出敌方的区域  
                if type=='s' or type=='a': #筛选需要进行反投影的区域

                    if shape_type=='r': #绘制矩形
                        #引入左上右下两点
                        lt=self._region[r][:2].copy()
                        rd=self._region[r][2:4].copy()
                        #因原点不同，进行坐标变换
                        lt[1]=self._real_size[1]-lt[1]
                        rd[1]=self._real_size[1]-rd[1]
                        #另外两点坐标
                        ld=[lt[0],rd[1]]
                        rt=[rd[0],lt[1]]

                        cor=np.float32([lt,rt,rd,ld]).reshape(-1,2)#坐标数组

                        if height_type=='a': #四点在同一高度
                            height=np.ones((cor.shape[0], 1)) * self._region[r][4]
                        if height_type=='d': #四点在不同高度
                            height=np.ones((cor.shape[0], 1))
                            height[1:3] *= self._region[r][5]  # 右上和右下
                            height[[0, 3]] *= self._region[r][4]  # 左上和左下

                    if shape_type == 'fp':
                        # 四点凸四边形类型，原理同上
                        cor=np.float32(self._region[r][:8]).reshape(-1,2)
                        cor[:,1] -= self._real_size[1]  # 同上，原点变换
                        if height_type=='a':
                            height=np.ones((cor.shape[0],1))*self._region[r][8]
                        if height_type=='d':
                            height=np.ones((cor.shape[0],1))
                            height[1:3] *= self._region[r][9]  
                            height[[0, 3]] *= self._region[r][8] 

                    cor=np.concatenate([cor,height],axis=1)#合并高度坐标

                    recor=cv2.projectPoints(cor,self._rvec,self._tvec,self._K_0,self._C_0)[0].astype(int).reshape(-1,2)#得到反投影坐标
                    self._scene_region[r]=recor #储存反投影坐标
        self._scene_init=True
        return self._scene_region
    def push_T(self,rvec,tvec):
        '''
        输入相机位姿（世界到相机）
        return:相机到世界变换矩阵（4*4）,相机世界坐标
        '''
        self.rvec=rvec
        self.tvec=tvec
        #初始化预警区域字典
        self._plot_regin()
        T=np.eye(4)
        T[:3,:3]=cv2.Rodrigues(rvec)[0]#旋转向量转化为旋转矩阵
        T[:3,3]=tvec.reshape(-1)#加上平移向量
        T=np.linalg.inv(T)#矩阵求逆
        return T,(T@(np.array([0,0,0,1])))[:3]
    def update(self,frame):
        '''
        更新一帧
        '''
        self._every_scene = frame
    def check(self,armors,cars):
        '''
        预警预测
        armors:N,cls+对应的车辆预测框序号+装甲板bbox
        cars:N,cls+车辆bbox
        '''
        rp_alarming=None
        color_bbox = []
        cache=None  # 当前帧缓存框
        id=np.array([1,2,3,4,5])
        f_max=lambda x, y: (x+y+abs(x-y))//2
        f_min=lambda x, y: (x+y-abs(x-y))//2
        if isinstance(armors,np.ndarray) and isinstance(cars,np.ndarray):
            assert len(armors)
            pred_cls=[]
            p_bbox=[]  # IoU预测框（装甲板估计后的装甲板框）
            cache_pred=[]# 可能要缓存的当帧预测IoU预测框的原始框，缓存格式 id,x1,y1,x2,y2
            cache=np.concatenate([armors[:,0].reshape(-1,1),np.stack([cars[int(i)] for i in armors[:,1]],axis=0)],axis=1)
            cls=armors[:,0].reshape(-1,1)
            #以下为IOU预测
            if isinstance(self._cache, np.ndarray):
                for i in id:
                    mask=self._cache[:,0]==i
                    if not (cls==i).any() and mask.any():
                        cache_bbox=self._cache[mask][:,2:]
                        # 计算交并比
                        cache_bbox=np.repeat(cache_bbox,len(cars),axis=0)
                        x1=f_max(cache_bbox[:,0],cars[:,1])  # 交集左上角x
                        x2=f_min(cache_bbox[:,2],cars[:,3])  # 交集右下角x
                        y1=f_max(cache_bbox[:,1],cars[:,2])  # 交集左上角y
                        y2=f_min(cache_bbox[:,3],cars[:,4])  # 交集右下角y
                        overlap=f_max(np.zeros((x1.shape)),x2-x1)*f_max(np.zeros((y1.shape)),y2-y1)
                        union=(cache_bbox[:,2]-cache_bbox[:,0])*(cache_bbox[:,3]-cache_bbox[:,1])
                        iou=(overlap/union)

                        if np.max(iou) > self._iou_threshold:  # 当最大iou超过阈值值才预测
                            now_bbox=cars[np.argmax(iou)].copy()  # x1,y1,x2,y2
                            # TODO:可以加入Debug

                            # 装甲板位置估计
                            now_bbox[3]=now_bbox[3]//3
                            now_bbox[4]=now_bbox[4]//5
                            now_bbox[2]+=now_bbox[4]*3
                            now_bbox[1]+=now_bbox[3]
                            # TODO：Debug绘制装甲板

                            pred_cls.append(np.array(i))#预测出的装甲板类型
                            p_bbox.append(now_bbox[:,1:].reshape(-1,4))#预测出的bbox

            if len(pred_cls):
                # 将cls和四点合并
                pred_bbox=np.concatenate([np.stack(pred_cls,axis=0).reshape(-1,1),np.stack(p_bbox,axis=0)],axis=1)
            #默认使用bounding box为points四点
            x1=armors[:,2].reshape(-1,1)
            y1=armors[:,3].reshape(-1,1)
            x2=(armors[:,2]+armors[:,4]).reshape(-1,1)
            y2=(armors[:,3]+armors[:,5]).reshape(-1,1)
            points=np.concatenate([x1, y1, x2, y1, x2, y2, x1, y2],axis=1)
            #对仅预测出颜色的敌方预测框进行数据整合
            for i in cars:
                if i[0]==0:
                    color_bbox.append(i)
            if len(color_bbox):
                color_bbox=np.stack(color_bbox,axis=0)
            if isinstance(color_bbox,np.ndarray):
                #预估装甲板位置，见技术报告
                color_cls=color_bbox[:,0].reshape(-1,1)
                color_bbox[:,3]=color_bbox[:,3]//3  
                color_bbox[:,4]=color_bbox[:,4]//5
                color_bbox[:,1]+=color_bbox[:,3]
                color_bbox[:,2]+=color_bbox[:,4]*3
                x1=color_bbox[:,1]
                y1=color_bbox[:,2]
                x2=x1+color_bbox[:,3]
                y2=y1+color_bbox[:,4]
                color_fp=np.stack([x1,y1,x2,y1,x2,y2,x1,y2],axis=1)
                #与之前的数据进行整合
                points=np.concatenate([points,color_fp],axis=0)
                cls=np.concatenate([cls,color_cls],axis=0)
            points=points.reshape((-1,4,2))
            for r in self._scene_region.keys():
                # 判断对于各个预测框，是否有点在该区域内
                mask=np.array([[is_inside(self._scene_region[r],p) for p in cor] for cor in points])
                mask=np.sum(mask,axis=1)>0 #True or False,只要有一个点在区域内，则为True
                alarm_target=cls[mask] #需要预警的装甲板种类
                if len(alarm_target):
                    rp_alarming={r:alarm_target.reshape(1,-1)}
        #储存为上一帧的框
        if isinstance(cache, np.ndarray):
            for i in id:
                assert cache[cache[:,0]==i].reshape(-1,6).shape[0]<=1
            self._cache=cache.copy()
        else:
            self._cache=None
        return rp_alarming,pred_bbox
