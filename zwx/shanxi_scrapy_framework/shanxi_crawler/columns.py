DEFAULT_VALUE = "无"
SITE_NAME = "山西省公共资源交易平台"

COLUMNS = [
    "公告类型", "项目名称", "所属行业", "组织形式", "开标时间", "标书发售时间", "公告内容",
    "招标人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式",
    "监督部门", "监督部门地址", "监督部门联系人", "监督部门联系方式",
    "依据文件", "依据文号", "发布日期", "发布网站", "公告历史",
]

NOTICE_REQUIRED_FIELDS = [
    "开标时间", "标书发售时间", "公告内容",
    "招标人", "招标人地址", "招标人联系人", "招标人联系方式",
    "招标代理机构", "招标代理机构地址", "招标代理机构联系人", "招标代理机构联系方式",
    "监督部门", "监督部门联系方式",
]

SUPERVISION_REQUIRED_FIELDS = ["监督部门", "监督部门联系方式"]


def empty_row(project_name: str = "") -> dict:
    row = {col: DEFAULT_VALUE for col in COLUMNS}
    row["项目名称"] = project_name or DEFAULT_VALUE
    row["发布网站"] = SITE_NAME
    row["依据文件"] = "无"
    row["依据文号"] = "无"
    return row
