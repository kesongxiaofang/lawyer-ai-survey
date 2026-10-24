# -*- coding: utf-8 -*-
"""
survey_routes.py  (v3：分流感知统计 + 分支筛选后台 + CSV 导出)
问卷接收模块（Flask Blueprint，独立文件）。legelai 主文件一行都不用改。
部署：上传本文件到 PA 与 legelai 同目录，在 WSGI 文件末尾加两行：
    from survey_routes import survey_bp
    application.register_blueprint(survey_bp)
然后 Reload。
查看数据： /view-survey    导出 CSV： /export-survey
分流感知：与前端 index.html 的 visible() 对齐——被分流跳过的题不算入"被问到"分母；
分支筛选：/view-survey 顶部可按 身份/态度/收费/业务 筛选，只看某一分支的数据与题目。
"""
import os, json, sqlite3, io, csv
from datetime import datetime
from collections import Counter, defaultdict
from flask import Blueprint, request, jsonify, Response

survey_bp = Blueprint('survey', __name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'survey_data.db')

QUESTIONS = json.loads('''
{
  "q1": {
    "title": "您目前的身份是？（单选）",
    "type": "single",
    "options": {
      "A": "执业律师",
      "B": "实习律师 / 律师助理",
      "C": "合伙人 / 权益合伙人",
      "D": "企业法务 / 其他法律从业者"
    }
  },
  "q2": {
    "title": "您日常工作中，占用时间最多的几类事务是？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "文书起草与校对（起诉状、合同、法律意见等）",
      "B": "证据整理、编清单、截图",
      "C": "法律法规 / 类案检索",
      "D": "立案、递交等程序性跑腿",
      "E": "客户沟通与接待",
      "F": "会议纪要、案卷归档等事务性工作",
      "G": "其他："
    }
  },
  "q3": {
    "title": "除了帮您干活，您是否希望AI也能帮您“学专业”——比如学着像资深律师一样拆解案件、解释办案思路、给出可参考的范本？（单选）",
    "type": "single",
    "options": {
      "A": "非常希望，这正是我目前欠缺的",
      "B": "希望，但更看重它先把活干好",
      "C": "无所谓",
      "D": "不需要，我自己学就好"
    }
  },
  "q4": {
    "title": "您当前最花精力的“非直接办案”事务是？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "案源拓展 / 客户获取",
      "B": "现有客户关系维护",
      "C": "团队管理与人手安排",
      "D": "财务、成本与利润核算",
      "E": "合规与执业风险管控",
      "F": "个人 / 律所品牌与专业形象",
      "G": "其他："
    }
  },
  "q5": {
    "title": "在团队管理上，您最希望AI帮上什么？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "统一团队文书的质量与风格（减少新人写歪）",
      "B": "新人培训 / 带教（给模板、陪练）",
      "C": "案件进度与关键期限监控（避免遗漏）",
      "D": "成本与利润核算（看清每个案子赚不赚钱）",
      "E": "自动生成客户进展报告",
      "F": "其他："
    }
  },
  "q6": {
    "title": "您日常办案中，最希望减少的“重复、低价值但耗时间”的工作是？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "证据整理与编清单",
      "B": "各类文书初稿起草",
      "C": "法规 / 类案检索与核对",
      "D": "与客户反复沟通的文案",
      "E": "程序性事务（立案、递交、期限盯对）",
      "F": "案卷归档等事务",
      "G": "其他："
    }
  },
  "q7": {
    "title": "您承办案件时，独立作业与和其他律师共同承办的大致比例是？（单选）",
    "type": "single",
    "options": {
      "A": "几乎都独立作业",
      "B": "独立作业为主，与其他律师共同承办次之",
      "C": "独立作业与其他律师共同承办各约一半",
      "D": "以与其他律师共同承办为主，独立作业为辅",
      "E": "几乎都与其他律师共同承办"
    }
  },
  "q8": {
    "title": "您的执业年限（或从事法律工作的年限）是？（单选）",
    "type": "single",
    "options": {
      "B": "3年以下",
      "C": "3~5年",
      "D": "5年以上"
    }
  },
  "q9": {
    "title": "您目前的主要业务领域是？（可多选）",
    "type": "multi",
    "options": {
      "A": "民商事诉讼（合同、借贷、侵权、交通事故/人损等一般民商事争议）",
      "B": "婚姻家事 / 继承",
      "C": "劳动争议 / 劳动仲裁",
      "D": "公司法 / 并购重组 / 证券资本市场",
      "E": "建设工程 / 房地产",
      "F": "知识产权（专利、商标、著作权、商业秘密）",
      "G": "刑事辩护 / 刑事合规",
      "H": "破产清算 / 执行 / 不良资产处置",
      "I": "金融 / 银行 / 保险 / 信托",
      "J": "涉外 / 国际商事",
      "K": "税务 / 行政争议",
      "L": "其他："
    }
  },
  "q10": {
    "title": "您所在团队（或律所）的人员构成更接近哪种？（单选）",
    "type": "single",
    "options": {
      "A": "独立执业，基本没有助理",
      "B": "小团队：1名合伙人/主办律师 + 1-2名律师或助理",
      "C": "中型团队：合伙人/主办律师 + 2-5名律师与助理",
      "D": "大所 / 20人以上大团队"
    }
  },
  "q11": {
    "title": "您日常业务中，哪一类工作占的比重更大？",
    "type": "single",
    "options": {
      "A": "以诉讼 / 仲裁等争议解决为主",
      "B": "以非诉 / 顾问业务为主"
    }
  },
  "q12": {
    "title": "您在日常工作中使用AI工具的情况是？（单选）",
    "type": "single",
    "options": {
      "A": "几乎每天都在用",
      "B": "经常用（每周几次）",
      "C": "偶尔用（每月几次）",
      "D": "注册过，但基本没用",
      "E": "从没用过"
    }
  },
  "q13": {
    "title": "您用过或听说过的AI工具有哪些？（可多选）",
    "type": "multi",
    "options": {
      "A": "通用对话AI（如DeepSeek、豆包、ChatGPT、通义千问、文心一言等）",
      "B": "通用AI的深度搜索/联网功能",
      "C": "专业法律AI产品（如得理、法行宝、秘塔法律、Alpha、法智等）",
      "D": "AI办公工具（如WPS AI、钉钉AI等）",
      "E": "别人定制的AI助手 / 知识库问答工具",
      "F": "AI技能包 / Skill（下载后装进通用AI，让它在某个业务上变专业）"
    }
  },
  "q14": {
    "title": "您用AI工具主要做过哪些事？（可多选，已合并同类项；没用过的可跳过）",
    "type": "multi",
    "options": {
      "A": "法律检索（法条查找核对、类案检索与裁判观点整理）",
      "B": "起草诉讼文书（起诉状、答辩状、代理词等）",
      "C": "起草非诉文书（合同、律师函、交易文件等）",
      "D": "修改 / 润色 / 校对文书",
      "E": "合同审查（标风险点、给修改建议、比对版本）",
      "F": "证据与事实整理（编证据清单、从聊天记录/截图/录音提取要点）",
      "G": "计算类工作（赔偿金、利息、违约金、时效等）",
      "H": "程序性问题解答（期限、管辖、材料清单、流程）",
      "I": "案情分析与专题研究（诉讼策略讨论、检索研究报告）",
      "J": "报告与事务性工作（办案/尽调/结案报告、会议纪要、翻译、PPT表格）",
      "K": "客户沟通（接待话术、答疑、进展告知文案）",
      "L": "知识管理（所内资料整理成可检索知识库）",
      "M": "以上都没用过",
      "其他": "其他："
    }
  },
  "q15": {
    "title": "总体来说，您觉得现在这些AI工具好用吗？（单选）",
    "type": "single",
    "options": {
      "A": "挺好用",
      "B": "有些环节好用，但毛病不少",
      "C": "噱头大于实用",
      "D": "基本没用，不如自己来",
      "E": "没用过，不好评价"
    }
  },
  "q16": {
    "title": "无论您目前是否使用过AI工具，您对“进一步了解或使用AI辅助办案”的态度是？",
    "type": "single",
    "options": {
      "A": "有兴趣，愿意继续聊聊需求",
      "B": "说不上兴趣，但如果有现成好用的，我愿意试",
      "C": "基本没兴趣，短期内不会用"
    }
  },
  "q17": {
    "title": "以下办案环节，哪些占用了您（或您团队）最多时间？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "证据材料整理（翻材料、截图、编证据清单）",
      "B": "各类文书起草（报告、起诉状、答辩状、申请书等）",
      "C": "法律法规和类案检索",
      "D": "当事人沟通与接待",
      "E": "调查取证（调流水、查档案、跑手续）",
      "F": "账册/财务材料核对",
      "G": "立案、递交等程序性跑腿事务"
    }
  },
  "q18": {
    "title": "具体到下面这些场景，如果AI能做到“基本能用、您把关确认”，哪些您愿意交给它做？",
    "type": "multi",
    "options": {
      "A": "从聊天记录、截图中自动识别并生成证据清单",
      "B": "按固定格式起草或续写办案报告（破产/重整、尽调、结案报告）",
      "C": "起诉状、答辩状、代理词等标准诉讼文书初稿",
      "D": "银行流水 / 账务初步筛查（标出可疑大额、异常转移、财产线索）",
      "E": "类案检索并整理裁判观点摘要",
      "F": "劳动仲裁请求计算（工资、赔偿金、加班费、工伤待遇）",
      "G": "交通事故 / 人损赔偿自动计算与索赔清单生成",
      "H": "合同审查：标出风险条款、给出修改建议",
      "I": "并购 / 投融资交易文件（SPA、SHA、尽调清单）起草与审阅辅助",
      "J": "证券 / 资本市场：招股书、反馈回复、合规报告素材整理与初稿",
      "K": "建设工程：工程量、签证、工期争议材料梳理与金额计算",
      "L": "知识产权：侵权比对、权利要求解读、检索报告初稿",
      "M": "税务 / 行政争议：法条适用分析、处罚裁量论证辅助",
      "N": "涉外合同条款审查、域外法查明摘要",
      "O": "一站式计算与清单：从合同/材料识别赔偿金、利息等公式并计算，同步整理证据清单",
      "P": "庭审提纲、质证意见、辩论意见初稿",
      "Q": "程序性问题解答（期限、管辖、材料清单、流程）",
      "R": "类案量刑检索、会见提纲",
      "S": "都不愿意交给AI（以上均不需要）",
      "其他": "其他："
    }
  },
  "q20": {
    "title": "您（或团队）不愿意交给AI的事，主要顾虑是什么？（可多选）",
    "type": "multi",
    "options": {
      "A": "准确性要求太高，AI出错代价大",
      "B": "涉及敏感判断 / 专业经验，AI替代不了",
      "C": "责任风险，出错算不清是谁的锅",
      "D": "案件信息保密，不能交给外部AI",
      "E": "流程难以标准化，AI学不会",
      "F": "其他："
    }
  },
  "q22": {
    "title": "您有没有因为任务太多、太急，忘记过某个期限或漏掉过某件事？（单选）",
    "type": "single",
    "options": {
      "A": "经常有，忙起来顾不上看记录",
      "B": "偶尔有",
      "C": "很少",
      "D": "从来没有"
    }
  },
  "q23": {
    "title": "如果AI能这样帮您管任务和期限：您把任务随手记给它（打字、语音，甚至转发微信/钉钉消息），它自动识别事项和截止时间（如上诉期、举证期限），再像手机通知一样提前1天、3天或5个工作日主动提醒您——您觉得怎么样？（单选）",
    "type": "single",
    "options": {
      "A": "很需要，这正是我的痛点",
      "B": "有用，但担心它识别不准期限",
      "C": "无所谓，现有待办工具够用",
      "D": "不需要"
    }
  },
  "q24": {
    "title": "用AI审合同（或其他文书）时，您更希望它怎么配合您？（单选）",
    "type": "single",
    "options": {
      "A": "能记住我的审查习惯和风格（我改过它的建议后，下次就按我的来），越用越顺手",
      "B": "每次都按通用的全面标准来，我自己再筛",
      "C": "只要能按我每次给的具体要求来就行",
      "D": "说不清"
    }
  },
  "q25": {
    "title": "如果有人针对您的业务方向，定制一个专属AI助手（比如懂破产报告怎么写、会按法院认可的风格生成材料），您的态度是？（单选）",
    "type": "single",
    "options": {
      "A": "很感兴趣，愿意尽快试用",
      "B": "有兴趣，但想先看别人用得怎么样",
      "C": "观望，除非确实证明可靠",
      "D": "不感兴趣，AI帮不上我的业务"
    }
  },
  "q26": {
    "title": "您对把AI用在办案上，最大的顾虑是什么？（最多选3项）",
    "type": "multi",
    "options": {
      "A": "AI会编造法条、案例（幻觉问题），核实太费劲",
      "B": "案件信息、当事人隐私可能泄露",
      "C": "出了错算谁的？责任归属不清",
      "D": "不够懂法律专业，输出质量差",
      "E": "不懂我的需求，答非所问，得反复纠正",
      "F": "价格贵，不值得",
      "G": "不会用，学习成本高",
      "H": "没什么顾虑，好用就行",
      "其他": "其他："
    }
  },
  "q27": {
    "title": "关于数据保密，您能接受的方式是？（单选）",
    "type": "single",
    "options": {
      "A": "必须本地部署，案件数据不能出我的电脑",
      "B": "可以用云服务，但数据不能被存储和拿去训练",
      "C": "只要好用，无所谓",
      "D": "说不清，看具体情况",
      "E": "其他（可具体说明：）"
    }
  },
  "q28": {
    "title": "如果定制AI工具按下面的方式收费，您更能接受哪种？（单选）",
    "type": "single",
    "options": {
      "A": "一次性买断，永久使用",
      "B": "按月/按年订阅",
      "C": "按案件或按次付费",
      "D": "先免费试用，好用再谈",
      "E": "不愿意为此付费",
      "F": "其他（可具体说明：）"
    }
  },
  "q30": {
    "title": "您希望这类工具以什么形式提供？（可多选）",
    "type": "multi",
    "options": {
      "A": "独立网站，打开浏览器就能用",
      "B": "微信里就能用（小程序/公众号）",
      "C": "嵌入我常用的办公软件（Word、WPS等）",
      "D": "装在我自己电脑上的本地工具",
      "E": "无所谓，好用就行",
      "F": "其他（可具体说明：）"
    }
  },
  "q31": {
    "title": "您更希望以哪种“形态”获得这个工具？（单选）",
    "type": "single",
    "options": {
      "A": "一个完整、开箱即用的AI产品（打开就能用，不用管底层技术）",
      "B": "一个“技能包/Skill”：下载后装进我正在用的AI（如豆包、DeepSeek等），让那个AI在我这个业务上变专业",
      "C": "我自己希望的形式（可具体说明：）"
    }
  },
  "q32": {
    "title": "如果选技能包形式，通常需要您完成简单的安装设置（下载、导入、绑定到常用AI，一般几分钟），您更希望怎么完成这一步？（单选）",
    "type": "single",
    "options": {
      "A": "我自己就能搞定，给我个说明就行",
      "B": "希望有一步步的视频/图文教程跟着做",
      "C": "希望有人远程帮我装好、直接到手就能用",
      "D": "太麻烦的话我就不要了，最好开箱即用"
    }
  },
  "q33": {
    "title": "在您的日常办案中，最希望AI帮您解决的事情是什么？（填空）",
    "type": "text",
    "options": {}
  },
  "q34": {
    "title": "您有没有被AI“坑”过的经历（比如编造法条、答非所问）？或对法律AI工具还有什么想法建议？（填空，选填）",
    "type": "text",
    "options": {}
  },
  "q35": {
    "title": "欢迎留下您的联系方式（微信 / 手机号 / 邮箱等均可，选填），便于未来与您取得联系：",
    "type": "text",
    "options": {}
  },
  "q29A": {
    "title": "价格区间——第28题选A（一次性买断）",
    "type": "single",
    "options": {
      "1": "50元以内",
      "2": "50-150元",
      "3": "150-400元",
      "4": "400-1000元",
      "5": "1000-2000元",
      "6": "2000元以上"
    }
  },
  "q29B_month": {
    "title": "价格区间——第28题选B（月费）",
    "type": "single",
    "options": {
      "1": "10元以内",
      "2": "10-30元",
      "3": "30-80元",
      "4": "80-150元",
      "5": "150-300元",
      "6": "300元以上"
    }
  },
  "q29B_year": {
    "title": "价格区间——第28题选B（年费）",
    "type": "single",
    "options": {
      "1": "100元以内",
      "2": "100-500元",
      "3": "500-1500元",
      "4": "1500-3000元",
      "5": "3000元以上"
    }
  },
  "q29C": {
    "title": "价格区间——第28题选C（按件/按次）",
    "type": "single",
    "options": {
      "1": "2元以内",
      "2": "2-8元",
      "3": "8-20元",
      "4": "20-50元",
      "5": "50-100元",
      "6": "100元以上"
    }
  }
}
''')



def _qkey(x):
    # 支持带字母/下划线后缀的题号，如 q29A / q29B_month，排序时不报错
    digits = ''
    for ch in x[1:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return (int(digits) if digits else 9999, x[1:])

def _num_base(qid):
    # 取题号的数字部分：q29A->29, q29B_month->29, q17->17
    d = ''
    for ch in qid[1:]:
        if ch.isdigit():
            d += ch
        else:
            break
    return int(d) if d else 0

# ===== 分流/分支规则（与前端 index.html 的 visible() 严格对齐）=====
GROUP_Q = {'q2', 'q3', 'q4', 'q5', 'q6', 'q7'}          # 身份专属题
IDENTITY_Q = {                                          # q1 决定哪些身份题被问到
    'A': ['q2', 'q3'],     # 执业律师
    'B': ['q6', 'q7'],     # 实习 / 律师助理
    'C': ['q4', 'q5'],     # 合伙人
    'D': [],               # 企业法务：无专属题
}
Q29BLOCK = {'q29A', 'q29B_month', 'q29B_year', 'q29C'}

def asked_set(ans):
    '''返回该受访者【被问到】的题目集合（分流跳过的题不计入）。'''
    # 前端把每题都存成数组（单选取首项），这里统一取标量
    def gv(k):
        v = ans.get(k)
        if isinstance(v, list):
            return v[0] if v else None
        return v
    asked = set(QUESTIONS.keys())
    # 1) 身份分支：先移除全部身份专属题，再按 q1 加回对应组的
    id1 = gv('q1')
    asked -= GROUP_Q
    if id1 in IDENTITY_Q:
        asked |= set(IDENTITY_Q[id1])
    # 2) 态度分支：q16='C'(基本没兴趣) 跳过后半段 q17..q34（含 q29 价格子题）
    if gv('q16') == 'C':
        asked = {q for q in asked if not (17 <= _num_base(q) <= 34)}
    # 3) 收费->价格分支：q28 决定 q29 下哪个子题
    id28 = gv('q28')
    if id28 == 'A':
        asked -= {'q29B_month', 'q29B_year', 'q29C'}
    elif id28 == 'B':
        asked -= {'q29A', 'q29C'}
    elif id28 == 'C':
        asked -= {'q29A', 'q29B_month', 'q29B_year'}
    else:
        asked -= Q29BLOCK
    # 4) 形态分支：q31='B' 才问 q32
    if gv('q31') != 'B':
        asked -= {'q32'}
    return asked

def _filters():
    return {
        'g': request.args.get('g', ''),
        'att': request.args.get('att', ''),
        'pay': request.args.get('pay', ''),
        'biz': request.args.get('biz', ''),
        'hide': request.args.get('hide') == '1',
    }

def _match_filter(ans, f):
    def gv(k):
        v = ans.get(k)
        if isinstance(v, list):
            return v[0] if v else None
        return v
    if f['g'] and gv('q1') != f['g']:
        return False
    if f['att'] and gv('q16') != f['att']:
        return False
    if f['pay']:
        q28 = gv('q28') or ''
        if f['pay'] == 'A' and q28 != 'A': return False
        if f['pay'] == 'B' and q28 != 'B': return False
        if f['pay'] == 'C' and q28 != 'C': return False
        if f['pay'] == 'D' and q28 not in ('D', 'E', 'F'): return False
    if f['biz'] and gv('q11') != f['biz']:
        return False
    return True

def _qs():
    return sorted(QUESTIONS.keys(), key=_qkey)

def _filter_form(f):
    opts = {
        'g': [('', '全部'), ('A', '执业律师'), ('B', '实习/助理'), ('C', '合伙人'), ('D', '企业法务')],
        'att': [('', '全部'), ('A', '有兴趣'), ('B', '可试'), ('C', '没兴趣(跳过后续)')],
        'pay': [('', '全部'), ('A', '买断'), ('B', '订阅'), ('C', '按件'), ('D', '不愿付')],
        'biz': [('', '全部'), ('A', '诉讼'), ('B', '非诉')],
    }
    def sel(name):
        cur = f[name]
        parts = ['<select name="%s">' % name]
        for v, lab in opts[name]:
            parts.append('<option value="%s"%s>%s</option>' % (v, ' selected' if v == cur else '', lab))
        parts.append('</select>')
        return ''.join(parts)
    hide = ' checked' if f['hide'] else ''
    return ('<form method="get" class="filterbar"><span>按分支筛选：</span>'
            '<label>身份%s</label><label>态度%s</label><label>收费%s</label><label>业务%s</label>'
            '<label class="ck"><input type="checkbox" name="hide" value="1"%s> 隐藏分流跳过的题目</label>'
            '<button type="submit">筛选</button><a href="/view-survey">重置</a></form>'
            ) % (sel('g'), sel('att'), sel('pay'), sel('biz'), hide)


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS responses (id INTEGER PRIMARY KEY AUTOINCREMENT, survey_id TEXT, submitted_at TEXT, answers TEXT)')
    conn.commit()
    conn.close()
_init_db()

@survey_bp.route('/api/lawyer-survey', methods=['POST'])
def receive_lawyer_survey():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            data = request.form.to_dict()
        conn = sqlite3.connect(DB_PATH)
        conn.execute('INSERT INTO responses (survey_id, submitted_at, answers) VALUES (?,?,?)',
                     ('lawyer-ai', datetime.now().isoformat(), json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'msg': 'received'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@survey_bp.route('/export-survey')
def export_survey():
    f = _filters()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT id, submitted_at, answers FROM responses ORDER BY id').fetchall()
    conn.close()
    all_responses = [{'id': r[0], 't': r[1], 'a': json.loads(r[2])} for r in rows]
    responses = [r for r in all_responses if _match_filter(r['a'], f)]
    buf = io.StringIO()
    w = csv.writer(buf)
    qids = _qs()
    w.writerow(['编号', '提交时间'] + ['Q' + q[1:] for q in qids])
    for r in responses:
        ans = r['a']
        row = [r['id'], r['t']]
        for q in qids:
            v = ans.get(q, '')
            if isinstance(v, list):
                v = '；'.join(str(x) for x in v)
            row.append(v)
        w.writerow(row)
    return Response('\ufeff' + buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=survey_export.csv'})

@survey_bp.route('/view-survey')
def view_survey():
    f = _filters()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT id, submitted_at, answers FROM responses ORDER BY id DESC').fetchall()
    conn.close()
    all_responses = [{'id': r[0], 't': r[1], 'a': json.loads(r[2])} for r in rows]
    responses = [r for r in all_responses if _match_filter(r['a'], f)]

    def esc(s):
        return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    asked_count = defaultdict(int)
    answered = defaultdict(int)
    counts = defaultdict(Counter)
    texts = defaultdict(list)
    for resp in responses:
        asked = asked_set(resp['a'])
        for qid in asked:
            if qid not in QUESTIONS:
                continue
            vals = resp['a'].get(qid)
            if vals is None:
                continue
            if isinstance(vals, str):
                vals = [vals]
            vals = [v for v in vals if v not in ('', None)]
            asked_count[qid] += 1
            if not vals:
                continue
            answered[qid] += 1
            q = QUESTIONS[qid]
            if q['type'] == 'text':
                texts[qid].extend(str(v) for v in vals)
            else:
                for v in vals:
                    if v == '其他' or v in q['options']:
                        counts[qid][v] += 1
                    else:
                        counts[qid]['__txt__'] += 1
                        texts[qid].append(str(v))

    def fmt(qid, vals):
        if isinstance(vals, str):
            vals = [vals]
        q = QUESTIONS.get(qid)
        if not q:
            return '、'.join(map(str, vals))
        if q.get('special'):
            return '价格档位 ' + '、'.join(map(str, vals))
        if q['type'] == 'text':
            return '；'.join(str(v) for v in vals) if vals else '（空）'
        parts, fill = [], []
        for v in vals:
            if v == '其他':
                parts.append('其他')
            elif v in q['options']:
                parts.append(q['options'][v])
            else:
                fill.append(str(v))
        s = '、'.join(parts)
        if fill:
            s += '（具体：' + '；'.join(fill) + '）'
        return s if s else '（空）'

    def opt_row(label, cnt, pct):
        return ('<div class="opt-row"><span class="lbl">%s</span>'
                '<span class="barwrap"><span class="bar" style="width:%d%%"></span></span>'
                '<span class="count">%d</span><span class="pct">%.0f%%</span></div>'
                ) % (esc(label), int(pct), cnt, pct)

    def qcard(qid, title, y, x, body, note=''):
        return ('<div class="card"><div class="qtitle">Q%s. %s <span class="badge-n">%d 人被问到 · %d 作答</span></div>%s%s</div>'
                ) % (qid[1:], esc(title), y, x, body, ('<div class="muted">' + esc(note) + '</div>' if note else ''))

    any_filter = any([f['g'], f['att'], f['pay'], f['biz']])
    sum_parts = []
    for qid in _qs():
        q = QUESTIONS[qid]
        y = asked_count.get(qid, 0)
        x = answered.get(qid, 0)
        if any_filter and f['hide'] and y == 0:
            continue
        if q.get('special'):
            c = counts.get(qid, Counter())
            rows_html = ''.join(opt_row('档位 ' + str(v), cnt, (cnt / y * 100 if y else 0)) for v, cnt in c.most_common())
            sum_parts.append(qcard(qid, q['title'], y, x, rows_html, note='按所选收费方式对应的价格档位'))
            continue
        if q['type'] == 'text':
            items = texts.get(qid, [])
            body = ''.join('<div class="textval">%s</div>' % esc(x) for x in items) if items else '<div class="muted">暂无文字回答</div>'
            sum_parts.append(qcard(qid, q['title'], y, x, body))
            continue
        c = counts.get(qid, Counter())
        rows_html = ''.join(opt_row(lab, c.get(v, 0), (c.get(v, 0) / y * 100 if y else 0)) for v, lab in q['options'].items())
        if c.get('__txt__'):
            rows_html += '<div class="muted">另有 %d 条补充说明</div>' % c['__txt__']
        if texts.get(qid):
            _ot = ''.join('<div class="other-item">· %s</div>' % esc(t) for t in texts[qid])
            rows_html += '<div class="other-box"><div class="ob-title">其他项填写内容（%d 条）</div>%s</div>' % (len(texts[qid]), _ot)
        sum_parts.append(qcard(qid, q['title'], y, x, rows_html))
    summary_html = ''.join(sum_parts)

    detail_parts = []
    for resp in responses:
        asked = asked_set(resp['a'])
        items = []
        for qid in _qs():
            if qid not in asked or qid not in QUESTIONS:
                continue
            vals = resp['a'].get(qid)
            if isinstance(vals, str):
                vals = [vals]
            real = [v for v in (vals or []) if v not in ('', None)]
            if real:
                items.append('<div class="ans"><b>Q%s.</b> %s：%s</div>' % (qid[1:], esc(QUESTIONS[qid]['title']), esc(fmt(qid, real))))
            else:
                items.append('<div class="ans skip"><b>Q%s.</b> %s：<span class="muted">(被问到但未填)</span></div>' % (qid[1:], esc(QUESTIONS[qid]['title'])))
        skipped = len(set(QUESTIONS) - asked)
        note = (' <span class="muted">· 本份因分流未展示 %d 题</span>' % skipped) if skipped else ''
        if items:
            detail_parts.append('<div class="resp"><h3>编号 #%s · %s%s</h3>%s</div>' % (resp['id'], esc(resp['t']), note, ''.join(items)))
    detail_html = ''.join(detail_parts) if detail_parts else '<div class="muted">还没有提交数据</div>'

    page = (PAGE_TOP.replace('{TOTAL}', str(len(all_responses)))
            .replace('{HIT}', str(len(responses)))
            .replace('{CSVHREF}', '/export-survey?' + request.query_string.decode('utf-8', 'ignore'))
            .replace('{FILTER}', _filter_form(f))
            + summary_html + PAGE_MID + detail_html + PAGE_BOT)
    return page

PAGE_TOP = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>问卷数据后台</title>
<style>
*{box-sizing:border-box;}
body{font-family:'Microsoft YaHei','微软雅黑',sans-serif;max-width:1000px;margin:0 auto;padding:24px;color:#1f2328;background:#f6f8fa;line-height:1.6;}
h1{font-size:22px;margin:0 0 4px;}
.meta{color:#57606a;font-size:13px;margin-bottom:8px;}
.filterbar{background:#fff;border:1px solid #eaecef;border-radius:10px;padding:10px 14px;margin:10px 0;display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:13px;}
.filterbar label{display:inline-flex;align-items:center;gap:4px;}
.filterbar select{padding:3px 6px;border:1px solid #d0d7de;border-radius:6px;font-size:13px;}
.filterbar button{padding:4px 14px;background:#1f6feb;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;}
.filterbar a{color:#1f6feb;font-size:13px;text-decoration:none;margin-left:4px;}
.dl{display:inline-block;margin:4px 0 10px;padding:8px 16px;background:#2da44e;color:#fff;border-radius:8px;text-decoration:none;font-size:14px;}
.card{background:#fff;border:1px solid #eaecef;border-radius:12px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.04);}
.qtitle{font-weight:600;font-size:15px;margin-bottom:10px;}
.badge-n{font-size:12px;font-weight:400;color:#57606a;background:#eef1f4;border-radius:10px;padding:1px 8px;margin-left:6px;}
.opt-row{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:14px;}
.lbl{width:48%;min-width:220px;}
.barwrap{flex:1;background:#eaeef2;border-radius:4px;overflow:hidden;height:18px;}
.bar{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff);border-radius:4px;}
.count{width:44px;text-align:right;color:#57606a;font-size:13px;}
.pct{width:46px;color:#57606a;font-size:13px;}
.muted{color:#8b949e;font-size:13px;margin-top:6px;}
.resp{border:1px solid #eaecef;border-left:4px solid #1f6feb;border-radius:8px;padding:10px 14px;margin:12px 0;background:#fff;}
.resp h3{margin:0 0 8px;font-size:14px;color:#1f6feb;}
.ans{font-size:14px;margin:4px 0;}
.ans b{color:#1f6feb;}
.ans.skip{color:#8b949e;}
.textval{background:#fafbfc;border:1px dashed #d0d7de;border-radius:8px;padding:8px 10px;font-size:14px;white-space:pre-wrap;margin:4px 0;}
.other-box{background:#fff8e6;border:1px solid #f0d58c;border-radius:8px;padding:8px 12px;margin-top:8px;}
.ob-title{font-size:13px;font-weight:600;color:#9a6700;margin-bottom:4px;}
.other-item{font-size:13px;color:#5c4a16;margin:2px 0;}
</style></head>
<body>
<h1>律师法律AI工具调研 · 数据后台</h1>
<div class="meta">共收到 <b>{TOTAL}</b> 份问卷（当前筛选命中 <b>{HIT}</b> 份）</div>
{FILTER}
<a class="dl" href="{CSVHREF}">下载 CSV（Excel 可直接打开，按当前筛选导出）</a>
<h2>汇总统计</h2>
'''

PAGE_MID = '''<h2>逐份明细</h2>'''

PAGE_BOT = '''</body></html>'''
