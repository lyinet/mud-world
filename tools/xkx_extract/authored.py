"""原版 LPC 缺失描述时的自撰文案。

仅覆盖切片内源码确实没有 `long` 的 11 条（7 件物品 + 4 个 NPC）。
这些条目的 `desc_source` 标记为 `authored`，不参与 source 覆盖率，
也不冒充原版文本；source 覆盖率只统计 `desc_source = "lpc"` 的条目。
"""

AUTHORED_DESCS = {
    # 物品：杭州
    "/d/hangzhou/npc/obj/cloth": "一件洗得发白的粗布衣裳，针脚细密，是寻常人家最常穿的衣物。",
    "/d/hangzhou/npc/obj/goupi": "一块硝制过的狗皮，皮毛粗糙却厚实，披在身上倒也挡风。",
    "/d/hangzhou/npc/obj/marry_card": "一本描金的婚约，写着你与结发之人的名字，须得去红娘庄才能解除。",
    # 物品：嘉兴
    "/d/jiaxing/npc/obj/cloth": "一件半旧的粗布短衣，浆洗得干净，是嘉兴一带百姓的常服。",
    "/d/jiaxing/obj/cloth": "一件素色布衣，布料虽粗，却缝补得整整齐齐。",
    "/d/jiaxing/npc/flower_shoe": "一双绣着并蒂莲的软底小鞋，针线精巧，鞋面还带着淡淡的脂粉香。",
    "/d/jiaxing/obj/flower_shoe": "一双大红缎面的绣花小鞋，鞋尖缀着绒球，走起路来悄无声息。",
    # NPC：杭州
    "/d/hangzhou/npc/guanjia": "一位年过花甲的老管家，背略微驼，守在刘庄门内，见人先拱手问安。",
    "/d/hangzhou/npc/jiading": "一名短打装束的家丁，腰间系着布带，目光守着庄内动静。",
    "/d/hangzhou/npc/you-ke": "一个游兴正浓的年轻人，东张西望，时不时停下来打量泉边的景致。",
    "/d/hangzhou/npc/guest": "一位风尘仆仆的进香客，手里攥着香烛，神色虔诚。",
}
