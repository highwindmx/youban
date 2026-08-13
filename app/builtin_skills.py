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
