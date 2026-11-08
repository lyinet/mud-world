# 原版自定义 `die()` NPC 击杀奖励矩阵

范围：`tools/xkx_ref/src/xkx100/` 下 `d/ clone/ open/ npc/ questobj/` 中所有定义 `void die()` 的 NPC（157 个）。

## 系统性规则（先给结论）

- 原版**没有**普通怪“击杀经验”。`feature/damage.lpc:230` 取 `last_damage_from` 作为 `killer`，`:253` 调 `COMBAT_D->killer_reward`；`adm/daemons/combatd.lpc:1119-1183` 对 NPC 死者只在**任务匹配**时发经验/潜能/阅历，普通怪直接返回（`:1120` 非人类、`:1137` 无任务）。
- 自定义 `die()` 且**不调** `::die()` 的 NPC 根本不会进入 `killer_reward`；即便调了、且是普通怪，也仍然没有击杀经验。
- 因此“最后一击拿经验”只对**任务目标的击杀奖励**成立；普通怪的经验只来自逐招交手成长（`combatd.lpc:361-372/417-423/579-587`），与最后一击无关。
- 复刻侧现状：`src/wsim/actions/combat.py:254-258` 的 `combat_exp_add` 全部来自 `simulate_fight` 的逐招增量；死亡分支（`:148-220`）无任何击杀奖励。`content/xkx/npcs.json` 无 `kill_reward` 字段。**即复刻侧本来就没有给击杀经验，不需要“关掉”任何发放**；本矩阵用于防止未来回归。
- 复刻已建模的自定义 `die()` NPC：`/d/hangzhou/npc/wolfdog`（掉 `goupi`，无经验）、`/d/hangzhou/npc/laohu`（掉 `hugu` 到尸体，无经验）、`/d/hangzhou/npc/dushe`（无自定义 die）。其余 150+ 个不在当前九地点内容包内。

## 矩阵

| 文件 | NPC | 调 `::die()`（→ killer_reward） | 直接调 `killer_reward` | `die()` 内显式掉落 | 击杀经验 |
| --- | --- | --- | --- | --- | --- |
| `clone/animal/banjiu.lpc` | 斑鸠 | 否 | 否 | obj/banjiu-rou | 无 |
| `clone/animal/bmonkey.lpc` | 老猴子 | 否 | 否 | /d/shenlong/obj/hougan | 无 |
| `clone/animal/dufeng.lpc` | 毒蜂 | 否 | 否 | __FILE__ | 无 |
| `clone/animal/lamb.lpc` | 黄羊 | 否 | 否 | obj/yangrou, obj/yangpi | 无 |
| `clone/animal/laohu.lpc` | 老虎 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/animal/laoshu.lpc` | 老鼠 | 否 | 否 | obj/shurou | 无 |
| `clone/animal/lion.lpc` | 狮子 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/animal/pig.lpc` | 小猪 | 否 | 否 | obj/pork | 无 |
| `clone/animal/snake.lpc` | 毒蛇 | 否 | 否 | obj/shedan | 无 |
| `clone/animal/wolfdog.lpc` | 大狼狗 | 否 | 否 | obj/goupi | 无 |
| `clone/animal/xbaozi.lpc` | 小豹子 | 否 | 否 | obj/baodan | 无 |
| `clone/animal/yang.lpc` | 绵羊 | 否 | 否 | obj/yangrou2 | 无 |
| `clone/animal/yetu.lpc` | 野兔 | 否 | 否 | obj/turou | 无 |
| `clone/animal/zsnake.lpc` | 竹叶青 | 否 | 否 | NOSTRUM_DIR shedan | 无 |
| `clone/haojie/shashou.lpc` | 七杀门杀手 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `clone/haojie/tianshaxing.lpc` | 天杀星 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/misc/pet.lpc` | 坐骑 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/misc/pet0.lpc` | 坐骑 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/npc/dazongshi.lpc` | 大宗师 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/bwdhnpc.lpc` | 弟子 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/killer.lpc` | 黑衣人 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/killer1.lpc` | 黑衣人 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/questfeizei.lpc` | 飞贼 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/questxuncheng.lpc` | — | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `clone/quest/questyinshi.lpc` | — | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/baituo/npc/baitu.lpc` | 白兔 | 否 | 否 | /d/baituo/obj/turou | 无 |
| `d/baituo/npc/caihuashe.lpc` | 菜花蛇 | 否 | 否 | obj/sherou | 无 |
| `d/baituo/npc/he.lpc` | — | 否 | 否 | /d/baituo/obj/hdh | 无 |
| `d/baituo/npc/jinshe.lpc` | 金环蛇 | 否 | 否 | /d/baituo/obj/jindan | 无 |
| `d/baituo/npc/jumang.lpc` | 巨蟒 | 否 | 否 | /d/baituo/obj/shepi | 无 |
| `d/baituo/npc/mangshe.lpc` | 蟒蛇 | 否 | 否 | /d/baituo/obj/shepi | 无 |
| `d/baituo/npc/qingshe.lpc` | 竹叶青蛇 | 否 | 否 | /d/baituo/obj/qingdan | 无 |
| `d/baituo/npc/tiger.lpc` | 老虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/beijing/npc/aobai.lpc` | 鳌拜 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/beijing/npc/hai.lpc` | 海公公 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/beijing/npc/mao18.lpc` | 茅十八 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/binghuo/npc/bear.lpc` | — | 否 | 否 | /clone/medicine/nostrum/xiongdan | 无 |
| `d/binghuo/npc/haibao.lpc` | 海豹 | 否 | 否 | /clone/medicine/vegetable/lingzhi1, haibaorou, haibaopi | 无 |
| `d/binghuo/npc/shayu.lpc` | 鲨鱼 | 否 | 否 | /clone/medicine/vegetable/shadan | 无 |
| `d/chengdu/npc/yanliu.lpc` | 燕六 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/npc/tang.lpc` | 唐楠 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/npc/wolfdog.lpc` | 大狼狗 | 否 | 否 | obj/goupi | 无 |
| `d/city/npc/xunpu.lpc` | — | 是 | 否 | /d/choyin/npc/yamen_po.lpc | 仅任务匹配 |
| `d/city/task3/tufei1.lpc` | 盘龙花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/task3/tufei2.lpc` | 卧虎花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/task3/tufei3.lpc` | 飞凤花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/task3/tufei4.lpc` | 麒麟花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/task3/tufei5.lpc` | 神龟花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/city/task3/tufei6.lpc` | 仙鹤花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/dali/npc/cow.lpc` | 水牛 | 否 | 否 | /clone/medicine/vegetable/niuhuang | 无 |
| `d/dali/npc/laohu.lpc` | 孟加拉虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/dali/npc/qingshe.lpc` | 竹叶青蛇 | 否 | 否 | /d/baituo/obj/qingdan | 无 |
| `d/dali/npc/yetu.lpc` | 野兔 | 否 | 否 | turou | 无 |
| `d/emei/npc/jumang.lpc` | — | 否 | 否 | /clone/medicine/nostrum/shedan | 无 |
| `d/foshan/npc/chaofeng.lpc` | 老朝奉 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/foshan/npc/wolfdog.lpc` | 大狼狗 | 否 | 否 | obj/goupi | 无 |
| `d/fuzhou/npc/biaotou.lpc` | — | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/guanwai/npc/bear.lpc` | — | 否 | 否 | /clone/medicine/nostrum/xiongdan | 无 |
| `d/guanwai/npc/buck.lpc` | — | 否 | 否 | /clone/medicine/vegetable/lurong | 无 |
| `d/guanwai/npc/doe.lpc` | — | 否 | 否 | /clone/medicine/vegetable/lurong | 无 |
| `d/guanwai/npc/dongbeihu.lpc` | 东北虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/guanwai/npc/she.lpc` | — | 否 | 否 | /clone/medicine/vegetable/shexiang | 无 |
| `d/guiyun/npc/jiubao.lpc` | 酒保 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/guiyun/npc/snake.lpc` | 毒蛇 | 否 | 否 | /d/guiyun/obj/shedan | 无 |
| `d/guiyun/npc/zhanggui.lpc` | 掌柜 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/gumu/npc/yufeng.lpc` | — | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/hangzhou/npc/laohu.lpc` | 老虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/hangzhou/npc/wolfdog.lpc` | 大狼狗 | 否 | 否 | obj/goupi | 无 |
| `d/hanzhong/npc/wolfdog.lpc` | 大狼狗 | 否 | 否 | obj/goupi | 无 |
| `d/hanzhong/npc/wugui.lpc` | 乌龟 | 否 | 否 | /clone/medicine/vegetable/guijia | 无 |
| `d/hasake/npc/huilang.lpc` | 大灰狼 | 否 | 否 | __DIR__obj/langpi | 无 |
| `d/hasake/npc/toulang.lpc` | 头狼 | 否 | 否 | obj/langpi | 无 |
| `d/hasake/npc/yang.lpc` | 绵羊 | 否 | 否 | obj/yangpi | 无 |
| `d/huanggong/npc/hai.lpc` | 海公公 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/huashan/npc/lin-pingzhi.lpc` | 林平之 | 否 | 是 | /clone/book/bixie_book | 仅任务匹配 |
| `d/huijiang/npc/ox.lpc` | 牦牛 | 否 | 否 | obj/niujiao | 无 |
| `d/huijiang/npc/sheep.lpc` | 绵羊 | 否 | 否 | obj/yangrou | 无 |
| `d/jianzhong/npc/pusiqushe.lpc` | 菩斯曲蛇 | 否 | 否 | /clone/medicine/nostrum/guaishedan | 无 |
| `d/kunlun/npc/dog.lpc` | 车骑将军 | 否 | 否 | obj/goupi | 无 |
| `d/kunlun/npc/dog1.lpc` | 折冲将军 | 否 | 否 | obj/goupi | 无 |
| `d/kunlun/npc/dog2.lpc` | 平寇将军 | 否 | 否 | obj/goupi | 无 |
| `d/kunlun/npc/dog3.lpc` | 征东将军 | 否 | 否 | obj/goupi | 无 |
| `d/kunlun/npc/sheep.lpc` | 野山羊 | 否 | 否 | obj/yang-rou | 无 |
| `d/kunlun/npc/xueji.lpc` | 雪鸡 | 否 | 否 | obj/xueji-rou | 无 |
| `d/lingjiu/npc/lu.lpc` | 梅花鹿 | 否 | 否 | /clone/medicine/vegetable/lurong | 无 |
| `d/lingjiu/npc/yupopo.lpc` | 余婆婆 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/lingjiu/npc/zhang.lpc` | 獐 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/lingxiao/npc/laoban.lpc` | 陈朝奉 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/lingxiao/npc/snake.lpc` | 雪山怪蛇 | 否 | 否 | /clone/medicine/nostrum/guaishexue.lpc | 无 |
| `d/npc/m_weapon/tan/npc/fish.lpc` | 噬人鱼 | 否 | 否 | /d/npc/m_weapon/tan/item/zhenzhu | 无 |
| `d/npc/pet.lpc` | 坐骑 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/quanzhen/npc/deer.lpc` | — | 否 | 否 | /clone/medicine/vegetable/lurong | 无 |
| `d/quanzhen/npc/yunbao.lpc` | 云豹 | 否 | 否 | /clone/medicine/vegetable/baotai | 无 |
| `d/shaolin/npc/ad-laoshu.lpc` | 老鼠 | 否 | 否 | shurou | 无 |
| `d/shaolin/npc/bg-laoshu.lpc` | 老鼠 | 否 | 否 | shurou | 无 |
| `d/shaolin/npc/lao-shu.lpc` | 老鼠 | 否 | 否 | shurou | 无 |
| `d/shenlong/npc/fushe.lpc` | 腹蛇 | 否 | 否 | /d/shenlong/obj/shedan, /d/baituo/obj/shepi | 无 |
| `d/shenlong/npc/jinshe.lpc` | 金环蛇 | 否 | 否 | /d/baituo/obj/jindan | 无 |
| `d/shenlong/npc/lan.lpc` | 蓝凤凰 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/shenlong/npc/qingshe.lpc` | 竹叶青蛇 | 否 | 否 | /d/baituo/obj/qingdan | 无 |
| `d/shiliang/npc/fish.lpc` | 噬人鱼 | 否 | 否 | /d/npc/m_weapon/tan/item/zhenzhu | 无 |
| `d/suzhou/npc/wang.lpc` | 王合计 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/suzhou/npc/yetu.lpc` | 野兔 | 否 | 否 | turou | 无 |
| `d/tiezhang/npc/laohu.lpc` | 老虎 | 否 | 否 | /d/tiezhang/obj/hupi | 无 |
| `d/wanjiegu/npc/zhongling.lpc` | 钟灵 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/wudang/npc/cow.lpc` | — | 否 | 否 | /clone/medicine/vegetable/niuhuang | 无 |
| `d/wudang/npc/laohu.lpc` | 老虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/wudang/npc/yetu.lpc` | 野兔 | 否 | 否 | turou | 无 |
| `d/wudujiao/npc/chanchu1.lpc` | — | 是 | 否 | obj/chanchudu.lpc | 仅任务匹配 |
| `d/wudujiao/npc/chanchu2.lpc` | 莽牯朱蛤 | 是 | 否 | obj/chanchudu.lpc | 仅任务匹配 |
| `d/wudujiao/npc/chanchu3.lpc` | 三足金蟾 | 是 | 否 | obj/chanchudu.lpc | 仅任务匹配 |
| `d/wudujiao/npc/laohu.lpc` | — | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/wudujiao/npc/laohu2.lpc` | — | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/wudujiao/npc/she1.lpc` | — | 否 | 否 | obj/shedu.lpc | 无 |
| `d/wudujiao/npc/she2.lpc` | 铁线蛇 | 否 | 否 | obj/shedu.lpc | 无 |
| `d/wudujiao/npc/she3.lpc` | 腾蛇 | 否 | 否 | obj/shedu.lpc | 无 |
| `d/wudujiao/npc/wugong1.lpc` | — | 否 | 否 | obj/wugongdu.lpc | 无 |
| `d/wudujiao/npc/wugong2.lpc` | 金背蜈蚣 | 否 | 否 | obj/wugongdu.lpc | 无 |
| `d/wudujiao/npc/wugong3.lpc` | 阴山天蜈 | 否 | 否 | obj/wugongdu.lpc | 无 |
| `d/wudujiao/npc/xiezi1.lpc` | — | 否 | 否 | obj/xiezidu.lpc | 无 |
| `d/wudujiao/npc/xiezi2.lpc` | 食尸蝎 | 否 | 否 | obj/xiezidu.lpc | 无 |
| `d/wudujiao/npc/xiezi3.lpc` | 九尾蝎 | 否 | 否 | obj/xiezidu.lpc | 无 |
| `d/wudujiao/npc/zhizhu1.lpc` | — | 否 | 否 | obj/zhizhudu.lpc | 无 |
| `d/wudujiao/npc/zhizhu2.lpc` | 吸血蜘蛛 | 否 | 否 | obj/zhizhudu.lpc | 无 |
| `d/wudujiao/npc/zhizhu3.lpc` | 人面蜘蛛 | 否 | 否 | obj/zhizhudu.lpc | 无 |
| `d/wuxi/npc/feng.lpc` | 冯虎 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/xiakedao/npc/shizhe3.lpc` | 侍者 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/xiakedao/npc/shizhongyu.lpc` | 石中玉 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/xiakedao/npc/yezhu.lpc` | 野猪 | 否 | 否 | /d/xiakedao/obj/pigdie | 无 |
| `d/xiangyang/npc/qiuhang.lpc` | 邱航 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/xiangyang/task/killer.lpc` | 黑衣人 | 是 | 否 | —（走 make_corpse/背包） | 仅任务匹配 |
| `d/xiangyang/task/mengbing.lpc` | 蒙古大汉 | 否 | 否 | jianxi, mijian | 无 |
| `d/xiangyang/task/robber1.lpc` | 蒙古兵士 | 否 | 否 | robber1, robber2, robber3 | 无 |
| `d/xiangyang/task/robber2.lpc` | 蒙古将领 | 否 | 否 | robber1, robber2, robber3 | 无 |
| `d/xiangyang/task/robber3.lpc` | 蒙古高手 | 否 | 否 | robber1, robber2, robber3 | 无 |
| `d/xingxiu/npc/biaoshi.lpc` | 护镖镖师 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/xingxiu/npc/shachong.lpc` | 沙虫 | 否 | 否 | shachongke | 无 |
| `d/xingxiu/npc/snake.lpc` | 毒蛇 | 否 | 否 | obj/shedan | 无 |
| `d/xingxiu/npc/yang.lpc` | 绵羊 | 否 | 否 | yangpi | 无 |
| `d/xuedao/npc/xuebao.lpc` | 雪豹 | 否 | 否 | /clone/medicine/vegetable/baotai | 无 |
| `d/xueshan/npc/maoniu.lpc` | 牦牛 | 否 | 否 | /clone/medicine/vegetable/niuhuang | 无 |
| `d/xueshan/npc/yetu.lpc` | 野兔 | 否 | 否 | turou | 无 |
| `d/yanping/npc/huananhu.lpc` | 华南虎 | 否 | 否 | /clone/medicine/vegetable/hugu | 无 |
| `d/yixing/npc/bangzhong.lpc` | 帮众 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `d/yixing/npc/bangzhu.lpc` | — | 否 | 否 | BANGYIN | 无 |
| `d/yixing/npc/biaotou.lpc` | 镖头 | 否 | 否 | BIAOHUO | 无 |
| `open/task3/tufei1.lpc` | 盘龙花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/task3/tufei2.lpc` | 卧虎花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/task3/tufei3.lpc` | 飞凤花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/task3/tufei4.lpc` | 麒麟花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/task3/tufei5.lpc` | 神龟花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/task3/tufei6.lpc` | 仙鹤花纹 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/xiangyang/npc/qiuhang.lpc` | 邱航 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/xiangyang/task/killer.lpc` | 黑衣人 | 否 | 否 | —（走 make_corpse/背包） | 无 |
| `open/xiangyang/task/robber1.lpc` | 蒙古兵士 | 否 | 否 | robber1, robber2, robber3 | 无 |
| `open/xiangyang/task/robber2.lpc` | 蒙古将领 | 否 | 否 | robber1, robber2, robber3 | 无 |
| `open/xiangyang/task/robber3.lpc` | 蒙古高手 | 否 | 否 | robber1, robber2, robber3 | 无 |

> 说明：`是` 仅表示进入继承的 `feature/damage.lpc die()` 并最终调用 `killer_reward`；对普通 NPC 仍不发经验。`open/` 与 `d/xiangyang/` 等为不同任务副本，保留重复项以便审计。
