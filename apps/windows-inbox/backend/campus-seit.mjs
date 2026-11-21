const base='https://seit.sysu.edu.cn';
export const SEIT_OFFICIAL='学院官方';
export const SEIT_STUDENTS='社团与团学';

const organizations=[
 ['xsh','学院学生会','迎新、文体活动、学生权益与校园文化。'],
 ['tw','学院团委','团学通知、志愿服务、实践活动与组织建设。'],
 ['yjsh','学院研究生会','学术论坛、文体活动、经验交流与就业服务。'],
 ['xsxxkj','学生信息科技发展中心（信发）','电子设计竞赛、科技科普与技能培训。'],
 ['xyfw','校友服务与职业发展中心（职发）','校友活动、职业规划与企业交流。'],
];

export const SEIT_ENTRIES=[
 ...organizations.map(([id,name,note])=>({id:'seit-org-'+id,name,note,url:`${base}/student/${id}-js`,sourceId:'seit-org-'+id,dynamicsUrl:`${base}/student/${id}`})),
 {id:'seit-org-study',name:'学业与发展指导工作室',url:`${base}/article/2930`,note:'学院第二课堂与“学业加油站”的官方介绍；相关活动汇总到学工通知和学生园地。'},
 {id:'seit-org-wechat',name:'学院公众号与中大SEIT学生园地',url:'https://jwb.sysu.edu.cn/article/901',note:'学校公开材料列出的两个发布渠道。可在微信搜索账号、核对认证后关注；已接收的图文会汇总。'},
];

export const SEIT_SOURCES=[
 ['news','学院新闻','/news','学院动态'],
 ['general','综合通知','/inform/events','学院动态'],
 ['undergrad','本科生教育通知','/undergraduate/inform','教学动态'],
 ['undergrad-docs','本科教务文件','/undergraduate/docs','教学动态'],
 ['graduate-notices','研究生教育通知','/graduate/inform','教学动态'],
 ['doctor','博士招生','/admission/doctor','招生升学'],
 ['hmt','港澳台研究生招生','/admission/graduate-hmt','招生升学'],
 ['majors','本科专业介绍','/admission/major','专业调整'],
 ['graduate-majors','研究生招生专业目录','/admission/graduate-major','专业调整'],
 ['admission-guide','本科招生简介','/admission/brochure','招生升学','page'],
 ['research-notices','科研动态与公示','/research/news','科研动态'],
 ['events','学术讲座','/events','学术会议'],
 ['student-notices','学工通知','/student/notice','学生工作'],
 ['student-rules','学工制度','/student/rules','学生工作','page'],
 ['student-activities','学生园地与学生活动','/student/activities','学生活动','list',SEIT_STUDENTS],
 ['student-style','班团风采','/student/style','学生活动','list',SEIT_STUDENTS,true],
 ['party-news','党建要闻','/dang/news','党建团学'],
 ['party-notices','党务通知','/dang/inform','党建团学'],
 ['party-branches','支部风采','/dang/zb','党建团学'],
 ['party-rules','党建规章制度','/dang/rules','党建团学'],
 ['party-learning','党建学习园地','/dang/xx','党建团学'],
 ['career-notices','就业通知','/career/notice','招聘就业','list',SEIT_OFFICIAL,true],
 ['career-list','校园招聘信息','/career/inform','招聘就业'],
 ['recruitment','师资与人才招聘','/recruitment','招聘就业'],
 ['alumni-notices','校友公告','/alumni/notice','校友动态'],
 ['alumni-news','校友动态','/alumni/news','校友动态'],
 ...organizations.map(([id,name])=>['org-'+id,name,`/student/${id}`,'学生活动','list',SEIT_STUDENTS]),
].map(([id,name,pathname,category,mode='list',section=SEIT_OFFICIAL,allowEmpty=false])=>({id:'seit-'+id,name:'学院 · '+name,url:base+pathname,category,mode,section,allowEmpty,priority:0}));

export function isEmptySeitListing(html){
 return /<div\b[^>]*data-block-plugin-id="block_content:ce571c56-9c2e-467e-88a8-9823419de643"[^>]*>\s*(?:<!--[^]*?-->\s*)?<\/div>/i.test(html);
}
