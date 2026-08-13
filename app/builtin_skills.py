"""友伴内置技能（预装能力）。

用户所说的「预装 skill」是指装进友伴本身，而非 WorkBuddy 技能市场。
这里把常用能力以「系统提示插件」形式内置：当本轮用户输入命中触发词，
自动在系统提示里追加该技能的专业指令，让模型按规范产出结果。

新增一个内置技能只需在此注册表的 BUILTIN_SKILLS 里加一项即可。
"""

BUILTIN_SKILLS: dict[str, dict] = {
    "meeting_minutes": {
        "name": "会议纪要",
        "triggers": [
            "会议纪要", "会议记录", "整理会议", "会议要点",
            "meeting minutes", "meeting note", "meeting summary",
        ],
        "system": (
            "【内置技能：会议纪要】\n"
            "当用户要求整理/生成会议纪要时，请基于用户提供的文字稿、笔记或要点，"
            "输出结构化 Markdown 纪要，严格包含以下小节：\n"
            "1. **会议信息**：会议主题、日期、时间、地点、主持人、参会人、记录人。\n"
            "2. **议程与讨论**：按议题分点，记录关键讨论、决策依据、分歧点。\n"
            "3. **决议事项**：明确达成的结论。\n"
            "4. **行动项（四要素表）**：| 事项 | 负责人 | 截止时间 | 交付物 |；"
            "负责人/时间/交付物缺失时填「（待确认）」，不得编造。\n"
            "5. **待决问题与风险**：尚未解决的事项与潜在风险。\n"
            "6. **下一步计划**：后续安排。\n"
            "纪律：只基于用户提供的内容；信息缺失用「（待确认）」标注，严禁幻觉式补全。"
        ),
    },
    "make_ppt": {
        "name": "AI PPT",
        "triggers": [
            "ppt", "幻灯片", "演示文稿", "课件", "做个ppt", "生成ppt",
            "slides", "deck", "presentation",
        ],
        "system": (
            "【内置技能：AI PPT 生成】\n"
            "当用户要求制作 PPT / 幻灯片 / 演示文稿 / 课件时，请：\n"
            "1. 先自行构思内容大纲（标题 + 分节），每页要点 3-6 条、每条简短（≤ 20 字为宜），"
            "避免整段文字堆砌；标题页单独成页。\n"
            "2. 调用 make_ppt 工具生成 .pptx：title 为总标题；"
            "slides 为每页列表，每项含 heading(页标题) 与 points(要点数组)。\n"
            "3. 生成后告知用户文件保存位置，并可建议下一步（如用 parse_document 回读核对、"
            "或在 PowerPoint 中微调排版）。\n"
            "纪律：内容须准确、自洽；若用户给了素材则基于素材，否则可基于你的知识生成，"
            "但不得编造具体数据/引用。"
        ),
    },
}


def detect_skill(text: str) -> str | None:
    """检测本轮用户输入是否命中某个内置技能。

    命中则返回该技能的 system 插件文本，未命中返回 None。
    """
    if not text:
        return None
    low = text.lower()
    for skill in BUILTIN_SKILLS.values():
        for kw in skill["triggers"]:
            if kw.lower() in low:
                return skill["system"]
    return None
