"""玩家对话的确定性回应生成。

模板回应必须：第一人称、20–60 字、直接回应玩家输入里的具体内容（人物/地点/
物品/动作），并把人物身份与性格融进一句话，而不是倾倒人格档案。本模块不读
墙钟、不用全局随机；同一个 (npc, message, tick, turn) 永远得到同一句话。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

PLAYER_ID = "player:visitor"
PLAYER_NAME = "访客"

# 只做轻量情感判定：够用、可解释、可回放；不引入模型。
POSITIVE_WORDS = ("谢谢", "多谢", "劳烦", "帮忙", "请", "辛苦", "好看", "厉害", "佩服", "夸")
NEGATIVE_WORDS = ("滚", "骗", "笨", "蠢", "废物", "骂", "偷", "赖账", "滚开")

# 人设卡里的居住地是英文 id，模板里换成可朗读的地点名。
LOCATION_NAMES = {
    "market": "集市",
    "smithy": "铁匠铺",
    "inn": "客栈",
    "escort_agency": "镖局",
    "pharmacy": "药铺",
    "academy": "书院",
    "ferry": "河渡",
    "temple": "城隍庙",
}

# 意图优先按语义类别分化：不同类别使用不同句式骨架，禁止只替换话题名词。
INTENT_RULES: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    ("rejection", (("不买", 5), ("不要", 4), ("不用", 4), ("算了", 4),
                   ("免了", 4), ("没兴趣", 5), ("滚", 5), ("走开", 4), ("别烦", 4),
                   ("拒绝", 5), ("信不过", 4), ("不照顾", 5), ("问句话", 3))),
    ("greeting", (("你好", 4), ("您好", 4), ("今日可好", 5), ("近来可好", 5),
                  ("气色", 4), ("幸会", 3), ("久仰", 3))),
    ("directions", (("哪里", 3), ("哪儿", 3), ("怎么走", 4), ("怎么去", 4),
                    ("落脚", 5), ("住处", 4), ("客栈", 3), ("迷路", 3),
                    ("石桥", 5), ("东街", 4), ("西街", 4), ("河渡", 5),
                    ("走多久", 5), ("井", 4), ("槐树", 4), ("城隍庙", 5),
                    ("后门", 4), ("通不通", 4), ("避开", 4), ("巡街", 3),
                    ("灯笼", 3), ("客栈门口", 5))),
    ("help", (("帮我", 4), ("帮忙", 4), ("劳烦", 4), ("求你", 4), ("能不能", 3),
              ("赏脸", 5), ("请你", 4), ("建议", 3), ("借", 2), ("学门手艺", 5),
              ("打听到", 5), ("找的人", 4), ("我要找", 4), ("肯不肯", 5),
              ("收我", 4), ("师傅", 3), ("顺路", 5), ("带我", 5), ("带一段", 4),
              ("办到", 5), ("哪一步", 5), ("办不成", 4), ("别的路", 4),
              ("几时得空", 4), ("磨了没有", 4))),
    ("credit", (("赊账", 6), ("赊", 4), ("借钱", 6), ("借我", 4), ("欠账", 5),
                ("欠", 3), ("账期", 5), ("还钱", 5), ("缓两天", 4), ("手头紧", 3),
                ("利钱", 6), ("利息", 6), ("字据", 6), ("立字据", 7),
                ("旧账", 5), ("欠下", 4), ("还一半", 5), ("先还", 4),
                ("下月清", 4), ("怎么还", 4), ("押", 3), ("抵押", 4),
                ("宽限", 5), ("熟客", 3), ("物件", 3))),
    ("gossip", (("听说", 3), ("新鲜事", 5), ("消息", 4), ("传闻", 4), ("外乡人", 5),
                ("招人", 4), ("官府", 2), ("加税", 2), ("最近", 2), ("镇上", 2),
                ("为人", 4), ("认识", 4), ("熟吗", 4), ("靠得住", 4), ("公道", 4),
                ("什么人", 3), ("谁最", 5), ("谁有钱", 5), ("哪家", 4),
                ("师爷", 3), ("捕快", 3), ("掌柜", 3), ("九娘", 3), ("三娘", 3),
                ("读书人", 4), ("科举", 5), ("当差", 5), ("年轻时", 4),
                ("营生", 4), ("手艺", 4), ("老屠户", 4), ("后来", 3),
                ("多少年", 3), ("后来去哪", 5), ("去哪了", 4))),
    ("price", (("肉价", 5), ("价钱", 4), ("价格", 4), ("多少钱", 4), ("几文", 4),
               ("本钱", 5), ("盘铺子", 5), ("下水", 4), ("瘦肉", 4), ("绣品", 4),
               ("卖", 3), ("买", 3), ("生意", 3), ("铺子", 3), ("贵", 3), ("斤", 2),
               ("便宜", 4), ("零头", 4), ("隔壁", 5), ("肥瘦", 4), ("搭", 3),
               ("切", 2), ("多给", 3), ("抹", 3), ("少买", 4), ("通融", 4),
               ("现钱", 4), ("价差", 4), ("卖的是什么", 5), ("卖什么", 5),
               ("老价钱", 5), ("常年", 4))),
    ("chitchat", (("平时", 4), ("除了", 3), ("家里", 4), ("还做什么", 4),
                  ("怎么切", 3), ("刀工", 4), ("跟谁学", 4), ("怕不怕", 5),
                  ("信命", 5), ("安全", 4), ("怪声", 4), ("夜里", 3), ("天黑", 3),
                  ("巡街", 2), ("这把刀", 5), ("把刀", 4), ("砍刀", 4), ("年头", 4),
                  ("以后", 4), ("将来", 4), ("会变", 5), ("会成", 4), ("往后", 3))),
)

# 每个骨架只表达一件事；跨类别不复用同一句尾口号或同一组语义内容。
TEMPLATES: dict[str, tuple[str, ...]] = {
    "greeting.a": ("我在呢，{role}这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。",),
    "greeting.b": ("来啦，我正守着摊子；做{role}的没别的本事，你有事尽管开口。",),
    "directions.a": ("找落脚处往东街走，过了石桥见红灯笼就是客栈；我摊子在{place}，走岔了回来问我。",),
    "directions.b": ("从{place}往北两条街，门挂蓝布帘的是客栈；路口老槐树作记号，走岔了回来问我。",),
    "directions.landmark.a": ("石桥有两座，东街那座过河，西街那座通巷；你要去客栈走东街，我摊子在集市那头。",),
    "directions.landmark.b": ("东街西街都有石桥，别走混了；我摊子在集市，认准红灯笼那家。",),
    "directions.inn_lamp.a": ("客栈门口灯笼夜里点，三更前不灭；我收摊时见过，你顺着亮走，别钻暗巷。",),
    "directions.inn_lamp.b": ("那灯笼天黑就挂上，雨夜也亮；看见灯就看见门，别摸黑乱撞。",),
    "directions.ferry.a": ("从我这摊到河渡，脚程半个时辰；推车慢些，过了石桥再往北。",),
    "directions.ferry.b": ("摊子到河渡不远，沿集市走到头，见渡口旗子就是；走快些赶上船。",),
    "directions.avoid_patrol.a": ("收摊后巡街的走东街，你绕西边巷子；我走那边熟，别打灯笼，脚步放轻。",),
    "directions.avoid_patrol.b": ("要避巡街，收摊就往河边走；那一带没灯，路熟再走。",),
    "directions.well.a": ("老槐树底下那口井还在，井沿塌了半边；我担水见过，夜里别靠近，白天去打水。",),
    "directions.well.b": ("井没填，水还清；槐树底下往南三步就是，我见过有人挂井绳。",),
    "directions.temple.a": ("城隍庙后门通集市，穿过香案旁的小门就是；我送肉走过，庙会时人多，走侧边。",),
    "directions.temple.b": ("后门开着，通到集市西头；你从庙墙绕过去，见肉摊就是我的。",),
    "gossip.person.reputation.a": ("{topic}这人我打过照面：嘴上是热络的，心里各有一本账；你跟这人来往，先护住钱袋。",),
    "gossip.person.reputation.b": ("{topic}我听过两说：有人夸这人仗义，有人说这人精算；我只信亲眼见的。",),
    "gossip.person.justice.a": ("{topic}办案我见过两回：嘴上讲公道，落笔看人情；你要递状子，先把证人备齐。",),
    "gossip.person.justice.b": ("{topic}断事不算糊涂，也不全按律条；我劝你有冤就去衙门，别只信旁人嚼舌。",),
    "gossip.person.relation.acquaintance.a": ("{topic}我打过几回照面，点头的交情；要托这人办事，先把丑话讲在前头。",),
    "gossip.person.relation.acquaintance.b": ("{topic}算认识，没深交；托人办事前，我先看这人办不办得成。",),
    "gossip.person.relation.closeness.a": ("{topic}跟我算不上熟，见面能说两句；这人底细我不清楚，你别把要紧事押上去。",),
    "gossip.person.relation.closeness.b": ("{topic}交情不深，喝过一回酒；真要共事，我还得再看看。",),
    "gossip.type.a": ("{topic}这号人我说不准：有老实念书的，也有拿字纸算人心的；你多留个心眼。",),
    "gossip.type.b": ("{topic}哪一类人都有好坏；我不看身份看做事，你相处久了自然知道。",),
    "gossip.impact.a": ("{topic}那边一乱，贩猪的车就少；肉价往上走，我买卖反倒多两单，心里并不踏实。",),
    "gossip.impact.b": ("{topic}那边不太平，路上查得紧；进货慢半拍，我只能抬价，赚的是辛苦钱。",),
    "gossip.passersby.a": ("外乡人前日过去两拨，都急着赶路；有一个打听客栈，我指了东街。",),
    "gossip.passersby.b": ("过路的外乡人不少，问价的多、落脚的少；我看这些人身上没多少盘缠。",),
    "gossip.recruit.a": ("招人入伙的话我听了一耳朵，说是镖局缺人手；工钱不高，你先别急着应。",),
    "gossip.recruit.b": ("我跟你说，是有人在招人，走镖的活计要签生死状；你家里没牵挂才去得。",),
    "gossip.event.a": ("街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。",),
    "gossip.event.b": ("这事我听到两耳朵，真真假假掺着；你要沾边，先把日子过稳。",),
    "gossip.who": ("财不露白，我不敢乱指名；你去钱庄门口站半日，看谁进出不打怵。",),
    "gossip.person.history.trade.a": ("{topic}年轻时在街口摆摊卖针线，后来才盘下铺面；我听说那会儿日子紧。",),
    "gossip.person.history.trade.b": ("{topic}早年的营生是替绣坊跑腿，风里来雨里去；这些旧事不大有人提。",),
    "gossip.person.history.post.a": ("{topic}以前在县衙当差，后来才调来镇上；我和这位打过两回照面。",),
    "gossip.person.history.post.b": ("{topic}早年在别处当差，听说管过狱；调到这儿之后，话比从前少。",),
    "gossip.person.exam.a": ("{topic}科举考过两三回，没中；我听说后来进书院抄书，才站稳脚。",),
    "gossip.person.exam.b": ("{topic}读的书不少，科举屡试不中；我劝你别当面提功名，怕是不愿说。",),
    "gossip.person.craft.a": ("{topic}的手艺是跟南边绣坊师傅学的；我听说学了好几年，针脚才细。",),
    "gossip.person.craft.b": ("{topic}做绣活的手艺，听说是娘家传的；不大提，我也不好细问。",),
    "gossip.person.relation.duration.a": ("{topic}跟我认识十来年了，早年一起跑过货；交情不算浅，也谈不上知己。",),
    "gossip.person.relation.duration.b": ("我和{topic}认识有些年头，见面能喝一碗；这些年各忙各的，走动少了。",),
    "gossip.person.whereabouts.a": ("老屠户后来搬去河渡那头，我听说替人看牲口；镇上就少见了。",),
    "gossip.person.whereabouts.b": ("老屠户走后没信儿，有人说到外乡投亲；我问过两回，没人说得准。",),
    "price.meat.a": ("肉价这条，我按今日行情走，一斤上下浮动两文；你诚心要，零头我抹，秤上不少你。",),
    "price.meat.b": ("肉价我这儿看货定价，好的贵两文，次的折一文；称完你验秤，短一两我赔。",),
    "price.meat.reason.a": ("肉价涨是上月猪少，贩子抬了价；这几日我这儿回落两文，你要买趁早。",),
    "price.meat.reason.b": ("价贵不怪我，进价就高；等下一车猪到了，我能让一文。",),
    "price.volume.a": ("铺子一天卖半扇猪，逢集多两成；肉价随行就市，我薄利走量。",),
    "price.volume.b": ("平日半扇，赶集能走一整扇；卖多卖少看天，我价不乱喊。",),
    "price.offal.a": ("下水今早留了一副，洗得干净；你要，我按斤称，晚了就没。",),
    "price.offal.b": ("一副刚收拾出来，心肝都在；要就趁早，我隔夜不留。",),
    "price.cut.a": ("瘦肉我教你半斤逆纹切薄片，快火一炒就嫩；要炖就切块，别拿刀背砸。",),
    "price.cut.b": ("瘦肉先剔筋膜再下刀，片要薄、丝要顺；我切半斤给你看，别切太厚。",),
    "price.other.a": ("{topic}不是我的行当，我只懂肉案；你要问这路货，得找做这行的行家，别让我瞎报价。",),
    "price.other.b": ("{topic}我没经手，价我说不准；你去问正经做这行的，别听我外行话。",),
    "price.capital.a": ("盘铺子的本钱，小门脸少说要二十两，押一付三；我劝你先盘账，再谈价。",),
    "price.capital.b": ("盘个铺子得备二十两上下，装修另算；我说实话，钱不够先合伙，别硬撑。",),
    "price.bargain.a": ("两文我让不了，最多抹个零头；你要诚心，称完我再搭一小块，别让我亏本。",),
    "price.bargain.b": ("价再压我就得赔秤；这样吧，零头抹掉，秤给你抹平，买卖图个回头。",),
    "price.bulk.a": ("整斤数我按批发价走，一斤让你半文；骨头另算，饶一根可以，别挑大骨。",),
    "price.bulk.b": ("买上几斤我让零头，整根骨头得算钱；搭头只给碎骨，你别嫌我抠。",),
    "price.mix.a": ("肥瘦搭着卖，我按七瘦三肥给你配；价照瘦肉走，肥的算添头。",),
    "price.mix.b": ("要肥瘦相搭，我先问你做啥菜；炒就多瘦，炖就带肥，价按整块算。",),
    "price.extra.a": ("零头不抹，多切两片可以；刀口偏一点，秤上我照实算。",),
    "price.extra.b": ("你要添两片，我切薄些给你；价不动，别让我再搭骨头。",),
    "price.compare.a": ("隔壁便宜自有别家的门道，我这肉当天进；你比价可以，先看秤干不干净。",),
    "price.compare.b": ("别家价低我不拦你；我这摊明码，短一两赔你，买谁的你自个掂量。",),
    "price.regular.a": ("常年照顾我，我记你个老价钱；每月头一回按旧价，别的日子随行。",),
    "price.regular.b": ("熟客我让一文，老价钱只能记一个月；你要认，我拿炭在账本上记你。",),
    "price.credit_diff.a": ("现钱买我让两文，赊半斤得按原价；账期一长，我还得担风险。",),
    "price.credit_diff.b": ("现钱当场清，价低；赊账要立账，价差就是利钱，别嫌我算得细。",),
    "price.goods.a": ("蓝布帘那家卖布匹针线，也收绣活；你要买肉，还是回我这摊。",),
    "price.goods.b": ("那铺子卖的是布和线，顺带接绣活；我一个屠户，只知道肉案上的事。",),
    "price.concession.a": ("少买半斤我照旧给你切，价不能动；通融是秤上多给你一钱。",),
    "price.concession.b": ("半斤就半斤，我不嫌少；价按整价走，你要再少，我只能搭碎肉。",),
    "credit.a": ("这笔可以赊，三天为限；第四天我上门讨，别让我空手回。",),
    "credit.b": ("我把话说明白：现钱付一半，余下三日清；到期不还，下回别再开口。",),
    "credit.origin.a": ("那三百文旧账是金掌柜替人垫的货款，利滚利拖到今；我认账，不赖。",),
    "credit.origin.b": ("旧账是那年进货短了钱，金掌柜垫上；一笔一笔都在账本上，我不糊涂。",),
    "credit.collateral.a": ("不赊账也行，你押件值钱的我收着；三日不赎，物件归我，丑话说前头。",),
    "credit.collateral.b": ("押东西可以，我只收能转手的；字据写明赎回日，过期别怪我不认。",),
    "credit.defer.a": ("账期到就得到，宽限一日我应；明日午前送来，别让我上门。",),
    "credit.defer.b": ("宽限可以，只延一天；再拖我就把账本给保正看，大家脸上不好。",),
    "credit.interest.a": ("利钱按月算，一百文取三文；不满一月按日折，我账本上写得清。",),
    "credit.interest.b": ("利钱不滚利，本金加月息；你若要立字据，我把数目当众念一遍。",),
    "credit.interest_regular.a": ("熟客赊账我免头一月利钱；过了月照旧收三文，熟归熟，账归账。",),
    "credit.interest_regular.b": ("老客我少收利钱，不免；赊得越多压得越久，我这小摊垫不起。",),
    "credit.repay.a": ("先还一半我收，余下写在下月；到时我照账本要，你别躲。",),
    "credit.repay.b": ("一半现钱一半下月清，我应；账上我记明白，少一文都不成。",),
    "credit.owe.a": ("我欠金掌柜三百文，打算卖了这扇猪就还；账挂在我名下，跑不了。",),
    "credit.owe.b": ("那三百文我认，先还一百，余下分两回；金掌柜那边我自己去说。",),
    "credit.ledger.a": ("这账要立字据，数额、日子、利钱都写上；空口无凭，我不放心。",),
    "credit.ledger.b": ("立字据我按手印，你留一份我留一份；日后有争执，拿纸说话。",),
    "chitchat.daily.a": ("卖完肉我收摊喝酒，偶尔去赌摊边站站；别的营生没本钱，也不眼馋。",),
    "chitchat.daily.b": ("除了守摊，我闲下来只喝酒；赌不沾，别的活计也插不上手。",),
    "chitchat.craft.origin.a": ("刀工是跟师傅学的，三年才出师；刀口深浅全靠手感，你看着简单，我练了半辈子。",),
    "chitchat.craft.origin.b": ("我师父脾气坏，骂了三年才放我出师；如今这刀口，是挨骂挨出来的。",),
    "chitchat.craft.knife.a": ("这把刀用了七八年，钝了就磨；刀口我从不外借，砍骨全靠它。",),
    "chitchat.craft.knife.b": ("刀是老师傅打的，钢口好；年头久了握柄发滑，我缠了布条。",),
    "chitchat.opinion.a": ("我不敢说看透，只知道天亮出摊、天黑收工；怕也挡不住事，先把今天过完。",),
    "chitchat.opinion.b": ("要问我怕不怕，我说怕也没用；该来的挡不住，先把今天的钱挣了。",),
    "chitchat.fate.a": ("命这事我不常想；该挣的钱照挣，该吃的苦照吃，怨天没用。",),
    "chitchat.fate.b": ("信不信命都得干活；我把今天过踏实，明天怎样交给天。",),
    "chitchat.safety.a": ("我夜里收摊先磨刀，再挑灯底下走；僻静巷子不踩，钱袋塞怀里。",),
    "chitchat.safety.b": ("夜里我不走暗巷，听见怪响就绕道；你要出门最好结伴，别省那两步路。",),
    "chitchat.sound.a": ("后墙根响过两回，像野猫又叫春；我提刀出去看，连个影都没有。",),
    "chitchat.sound.b": ("夜里听着像磨刀，又像风刮棚布；我绕过去查过，没见人。",),
    "chitchat.family.a": ("家里就我一个光棍，一人吃饱全家不饿；亲戚走动少，倒也清净。",),
    "chitchat.family.b": ("屋里没别人，就我守着这摊；逢年过节才回去看看老娘。",),
    "chitchat.future.a": ("往后我不敢乱猜，摊子撑一天算一天；攒下钱先清欠账，别的以后再说。",),
    "chitchat.future.b": ("这镇子往后怎样，我说不准；我只管把今天的肉卖完，明天的事明天愁。",),
    "help.a": ("这忙我搭把手可以，先讲清要办到哪一步；能应的我应，办不到我当面回你。",),
    "help.b": ("这忙我帮，但有条件：不沾赌、不赊账；你应得下，我明早就去。",),
    "help.skill.a": ("学手艺我劝你找口碑好的师傅，先打三年下手；别怕挨骂，手艺是磨出来的。",),
    "help.skill.b": ("想学手艺，我跟你说，先看师傅带不带人，再谈工钱；头一年只求站稳，别急着出师。",),
    "help.invite": ("喝酒我应了，地方你挑；酒钱各付各的，别灌我，喝多了我还得看摊。",),
    "help.schedule.a": ("抬案板我应了，后日收摊后得空；你别催，我把摊子收拾完就来。",),
    "help.schedule.b": ("这几日摊上忙，得空要等赶集后；你先把案板挪到门口，我路过搭手。",),
    "help.status.a": ("那把刀我磨了，刃口试过；你收摊前来拿，别搁在潮地方。",),
    "help.status.b": ("刀还没顾上磨，今早杀猪耽搁了；明日一早我给你磨好，误不了。",),
    "help.search.a": ("你要找的人我打听了，有人说往河渡去了；我再问两处，有信儿告诉你。",),
    "help.search.b": ("找人的事我问过客栈跑堂，还没准信；你再等一日，我顺路替你问。",),
    "help.referral.a": ("那位师傅收不收你，我不敢替人应；我领你去见一面，成不成看你的手艺。",),
    "help.referral.b": ("师傅脾气怪，收徒看眼缘；我替你递句话，你别空手去。",),
    "help.escort.a": ("明早我推车出摊，顺路带你一段；你到巷口等，别误了时辰。",),
    "help.escort.b": ("带你一程可以，车慢；你坐稳，过了石桥我就得拐去集市。",),
    "help.progress.a": ("你托的事我办到一半，还差最后一道；今日收摊前我给你回话。",),
    "help.progress.b": ("这事我正跑着，卡在保正那里；明日再去催，办成办不成我都告诉你。",),
    "help.alternative.a": ("我若办不成，就指你去镖局问；那边人面广，兴许有别的门路。",),
    "help.alternative.b": ("办不成我不瞒你，你去客栈找掌柜；掌柜认识的人多，别在我这棵树上吊死。",),
    "rejection.a": ("不买就不买，我不恼；摊子摆到天黑，你改主意再回来。",),
    "rejection.b": ("你不乐意就算了，强卖没意思；我记你一张脸，下回价照旧。",),
    "rejection.borrow.a": ("肉不买没事，刀我不外借；你要使刀，去铁匠铺租，别在我摊上试。",),
    "rejection.borrow.b": ("借刀不行，这刀跟我十几年；你要用，我帮你切，切完你拿走。",),
    "rejection.change_mind.a": ("改主意还来得及，肉还没下锅；你要多少，我照旧切。",),
    "rejection.change_mind.b": ("方才不要现在要，我不恼；摊上还有好肉，价照先前说的。",),
    "rejection.question.a": ("不照顾生意也成，问话我答；你问，我听着，别耽误我切肉。",),
    "rejection.question.b": ("买卖不成话还在，你问吧；我知道的说，不知道的不瞎编。",),
    "rejection.trust.a": ("我拒你不是信不过，是这规矩不能破；你按规矩来，我照应你。",),
    "rejection.trust.b": ("话说硬了你别多心；我信你，可账和刀都不兴含糊。",),
    "deflection.a": ("这话我没听出个头绪，你再说明白些，我才好接。",),
    "deflection.b": ("你说的我一时没摸着门道，换个说法，我再答你。",),
}

# 只有“回避”骨架保留人设台词；避免同一句口号跨类别复用。
QUOTE_HINTS: dict[str, tuple[str, ...]] = {}

# 具体话题优先于从原句里切出来的短语。
TOPIC_HINTS: tuple[tuple[str, str], ...] = (
    ("肉价", "肉价"), ("价钱", "价钱"), ("价格", "价钱"), ("多少钱", "价钱"),
    ("本钱", "本钱"), ("盘铺子", "铺子"), ("赊账", "赊账"), ("借钱", "借钱"),
    ("账期", "账期"), ("欠账", "欠账"), ("缓两天", "账期"), ("手头紧", "账期"),
    ("猪下水", "下水"), ("下水", "下水"), ("瘦肉", "瘦肉"), ("绣品", "绣品"),
    ("新鲜事", "新鲜事"), ("外乡人", "外乡人"), ("招人", "招人入伙"),
    ("北边", "北边"), ("怪声", "怪声"), ("加税", "加税"), ("官府", "官府"),
    ("信命", "命"), ("学门手艺", "学手艺"), ("喝碗酒", "喝酒"), ("喝酒", "喝酒"),
    ("卖多少肉", "行情"), ("卖多少", "行情"), ("铺子", "铺子"),
    ("家里", "家里的事"), ("平时", "平日营生"),
    ("除了", "平日营生"), ("刀工", "刀工"), ("这把刀", "这把刀"),
    ("把刀", "这把刀"), ("客栈", "落脚"),
)

TOPIC_DEFAULT = {
    "greeting": "这话",
    "directions": "落脚",
    "gossip": "新鲜事",
    "price": "这买卖",
    "credit": "赊账",
    "chitchat": "这话",
    "help": "这忙",
    "rejection": "买卖",
    "deflection": "这话",
}

_LEAD_WORDS = (
    "最后一个问题", "听说", "最近", "近来", "今日", "今天", "这镇上", "镇上",
    "你觉得", "你说", "我想问", "我想", "请问", "那么", "还有", "若是", "如果",
    "若有人", "若", "我初来乍到", "能帮我", "帮我", "劳烦", "能不能",
)

# 只去掉真正的呼语（“段大哥，”“三娘，”），不要把“借钱给你，……”这类内容截掉。
_VOCATIVE_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5]{0,2}(?:大哥|大姐|掌柜|师爷|捕快|先生|娘子|姑娘|老板|三娘|九娘))[，,]"
)

_TAIL_PATTERNS = (
    "为何", "为什么", "怎么", "如何", "怎么样", "多少", "几文", "多少钱",
    "可有什么", "有什么", "有没有", "知道吗", "认识吗", "熟吗", "靠得住吗",
    "公道吗", "肯赏脸吗", "怎么办", "怎么切最好", "怎么切", "是跟谁学的",
    "跟谁学的", "好不好", "行不行", "有些年头了", "看着有些年头了",
    "为人如何", "为人怎么样", "吗", "呢", "吧",
)


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = ""
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"\s*##\s+(.+)\s*$", line)
        if m:
            if current:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
            continue
        if current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def _clause(text: str, limit: int = 120) -> str:
    text = " ".join(x.strip() for x in text.splitlines() if x.strip())
    return text[:limit]


def _quote_usable(quote: str) -> bool:
    """过滤掉会出戏的台词：Markdown 标记、第三人称叙述。"""
    return not re.search(r"[他她]|\*\*|__|`|\n", quote)


def extract_persona_voice(text: str) -> dict[str, Any]:
    """从人设卡提取台词、说话风格与遇事反应倾向。"""
    quotes: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*>\s*(.+)$", line)
        if not m:
            continue
        value = m.group(1).strip().strip("“”\"'")
        if value and _quote_usable(value):
            quotes.append(value)
    sections = _sections(text)
    style = ""
    reaction = ""
    for title, body in sections.items():
        if "说话风格" in title:
            style = _clause(body)
        elif "遇事反应倾向" in title:
            reaction = _clause(body)
    return {"quotes": quotes, "style": style, "reaction": reaction}


def extract_persona_profile(text: str, npc_role: str = "") -> dict[str, str]:
    """只取模板需要的两件事：身份称谓与常驻地点，不读取整段档案。"""
    fields: dict[str, str] = {}
    for m in re.finditer(r"^-\s*([^：:]+)[：:]\s*(.+)$", text, re.M):
        fields.setdefault(m.group(1).strip(), m.group(2).strip())
    role = npc_role.strip()
    if not role:
        role = re.split(r"[，,]", fields.get("身份", ""))[0].strip()
    place = LOCATION_NAMES.get(fields.get("居住地", "").strip(), "")
    if not place:
        for name in LOCATION_NAMES.values():
            if name in fields.get("常出没", ""):
                place = name
                break
    return {"role": role or "手艺人", "place": place or "集市"}


def _sentiment(message: str, digest: str) -> tuple[float, float]:
    """返回 (affection_delta, trust_delta)；幅度 1 或 2。"""
    magnitude = 2.0 if int(digest[:2], 16) % 2 == 0 else 1.0
    pos = any(w in message for w in POSITIVE_WORDS)
    neg = any(w in message for w in NEGATIVE_WORDS)
    if pos and not neg:
        return magnitude, magnitude * 0.5
    if neg and not pos:
        return -magnitude, -magnitude * 0.5
    sign = 1.0 if int(digest[2:4], 16) % 2 == 0 else -1.0
    return sign * 1.0, sign * 0.5


def _intent(message: str) -> str:
    # 开头的“三娘，”“段大哥，”只是呼语，不参与意图打分。
    message = _VOCATIVE_RE.sub("", message or "")
    best = "deflection"
    best_score = 0
    for name, rules in INTENT_RULES:
        score = sum(weight for word, weight in rules if word in message)
        if score > best_score:
            best, best_score = name, score
    return best


_PERSON_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5]{1}(?:掌柜|师爷|捕快|九娘|三娘|老板|先生|娘子|姑娘))"
)
_PERSON_PLAIN_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,3})和你(?:认识|熟|打过交道|来往|共事)"
)


def _person_name(message: str) -> str:
    """只取具体人名/带姓称谓；“读书人”这类泛称不算人名。"""
    m = _PERSON_NAME_RE.search(message)
    if not m:
        m = _PERSON_PLAIN_RE.search(message)
    if not m:
        return ""
    return re.sub(r"^(?:和|与|跟|同)", "", m.group(1))


def _person_generic(message: str) -> str:
    """取泛称人群/身份词，走“人物类型”骨架，不冒充具体某人。"""
    for word in ("读书人", "书生", "货郎", "文人", "手艺人"):
        if word in message:
            return word
    return ""


def _object_type(message: str, intent: str) -> str:
    """识别“诉求对象”，与意图组成二维骨架选择。

    商品细分到肉/下水/切法/其他货；人物区分具体人名与泛称；另有地点、
    钱务、消息、杂务。泛称人物必须走 gossip.type，不能混进人名骨架。
    """
    if intent == "price":
        if any(w in message for w in ("本钱", "盘铺子", "盘个", "租金", "押金")):
            return "capital"
        if any(w in message for w in ("卖多少", "一天能卖", "走量", "能走多少")):
            return "volume"
        if any(w in message for w in ("猪下水", "下水", "心肝", "肚")):
            return "offal"
        if any(
            w in message for w in ("瘦肉", "肥肉", "五花", "里脊", "怎么切", "切法", "切最")
        ):
            return "cut"
        if any(w in message for w in ("绣品", "绣活", "布匹", "药材")):
            return "other"
        if any(w in message for w in ("为何", "为什么", "涨", "跌", "贵了", "便宜了", "回落")):
            return "reason"
        return "meat"
    if intent == "gossip":
        if any(w in message for w in ("谁最", "谁有钱", "哪家")):
            return "who"
        if any(w in message for w in ("招人", "入伙", "收人", "缺人手")):
            return "recruit"
        if any(w in message for w in ("外乡人", "过路", "经过", "路人")):
            return "passersby"
        if any(w in message for w in ("不太平", "受影响", "生意", "北边")):
            return "impact"
        if _person_name(message):
            return "person"
        if _person_generic(message):
            return "type"
        return "news"
    if intent == "chitchat":
        if any(w in message for w in ("家里", "亲人", "几口人", "老娘", "婆娘")):
            return "family"
        if any(w in message for w in ("怪声", "响动", "什么声音", "动静")):
            return "sound"
        if any(w in message for w in ("安全", "夜里", "天黑", "巡街", "暗巷")):
            return "safety"
        if any(w in message for w in ("以后", "将来", "会变", "会成", "往后")):
            return "future"
        if "信命" in message or "命" in message:
            return "fate"
        if any(w in message for w in ("怕不怕", "加税")):
            return "opinion"
        if any(w in message for w in ("刀工", "跟谁学", "学出来的", "哪儿学")):
            return "craft_origin"
        if any(w in message for w in ("这把刀", "把刀", "砍刀", "年头")):
            return "craft_knife"
        return "daily"
    if intent == "help":
        if any(w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")):
            return "invite"
        if any(w in message for w in ("学门手艺", "学手艺", "拜师", "收徒", "建议")):
            return "skill"
        return "errand"
    if intent == "directions":
        return "place"
    if intent == "credit":
        return "credit"
    if intent == "rejection":
        return "refusal"
    return "none"


def _demand(message: str, intent: str, obj: str) -> str:
    """在“意图 × 对象”之上再识别具体诉求，避免不同问题落进同一句骨架。"""
    if intent == "price":
        if obj in ("capital", "volume", "offal", "cut", "other", "reason"):
            return obj
        if any(w in message for w in ("现钱", "价差")):
            return "credit_diff"
        if any(w in message for w in ("老价钱", "常年", "熟客", "记个")):
            return "regular"
        if any(w in message for w in ("隔壁", "别家", "比你", "比价")):
            return "compare"
        if any(w in message for w in ("肥瘦", "搭着", "搭配")):
            return "mix"
        if any(w in message for w in ("少买", "少要", "通融", "减半")):
            return "concession"
        if "斤" in message and "半斤" not in message:
            return "bulk"
        if any(w in message for w in ("零头", "多给", "多切", "添", "饶")):
            return "extra"
        if any(w in message for w in ("卖的是什么", "卖什么", "卖啥")):
            return "goods"
        if any(w in message for w in ("便宜", "让", "抹", "砍", "压价", "两文")):
            return "bargain"
        return "meat"
    if intent == "credit":
        if any(w in message for w in ("旧账", "怎么欠", "欠下")):
            return "origin"
        if any(w in message for w in ("押", "抵押", "物件")):
            return "collateral"
        if any(w in message for w in ("宽限", "延期", "再缓", "缓", "账期到")):
            return "defer"
        if any(w in message for w in ("字据", "立据", "凭证")):
            return "ledger"
        if any(w in message for w in ("利钱", "利息")):
            if any(w in message for w in ("熟客", "老客")):
                return "interest_regular"
            return "interest"
        if any(w in message for w in ("怎么还", "你欠")):
            return "owe"
        if any(w in message for w in ("还", "还清", "先还", "下月")):
            return "repay"
        return "credit"
    if intent == "help":
        if any(w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")):
            return "invite"
        if any(w in message for w in ("学门手艺", "学手艺", "拜师", "收徒", "建议")):
            return "skill"
        if any(w in message for w in ("几时", "得空")):
            return "schedule"
        if any(w in message for w in ("磨了没有", "了没有", "办好了")):
            return "status"
        if any(w in message for w in ("打听", "找的人", "找到", "我要找")):
            return "search"
        if any(w in message for w in ("师傅", "收我", "肯不肯")):
            return "referral"
        if any(w in message for w in ("顺路", "带我", "带一段")):
            return "escort"
        if any(w in message for w in ("哪一步", "办到", "进展")):
            return "progress"
        if any(w in message for w in ("办不成", "别的路", "指条")):
            return "alternative"
        return "errand"
    if intent == "rejection":
        if any(w in message for w in ("改主意", "还来得及")):
            return "change_mind"
        if any(w in message for w in ("借把刀", "借刀", "使使")):
            return "borrow"
        if any(w in message for w in ("问句话", "总行")):
            return "question"
        if any(w in message for w in ("拒绝", "信不过")):
            return "trust"
        return "refusal"
    if intent == "directions":
        if any(w in message for w in ("石桥", "东街", "西街")):
            return "landmark"
        if any(w in message for w in ("灯笼", "亮不亮")):
            return "inn_lamp"
        if any(w in message for w in ("河渡", "走多久")):
            return "ferry"
        if any(w in message for w in ("避开", "巡街")):
            return "avoid_patrol"
        if any(w in message for w in ("井", "槐树")):
            return "well"
        if any(w in message for w in ("城隍庙", "后门", "通不通")):
            return "temple"
        return "place"
    if intent == "gossip":
        if obj == "person":
            if any(w in message for w in ("科举", "考过", "功名")):
                return "exam"
            if any(w in message for w in ("手艺", "跟谁学")):
                return "craft"
            if any(w in message for w in ("营生", "年轻时")):
                return "history_trade"
            if any(w in message for w in ("当差", "以前")):
                return "history_post"
            if "多少年" in message:
                return "duration"
            if any(w in message for w in ("办案", "公道", "断案", "官司")):
                return "justice"
            if any(w in message for w in ("认识", "打过交道")):
                return "acquaintance"
            if any(w in message for w in ("熟吗", "熟不熟", "交情")):
                return "closeness"
            return "reputation"
        if obj == "news":
            if any(w in message for w in ("后来去哪", "去哪了", "下落")):
                return "whereabouts"
            return "event"
    return ""


def _topic(message: str, intent: str) -> str:
    if intent == "directions":
        return "落脚"
    if intent == "credit":
        for word in ("赊账", "借钱", "账期", "欠账", "手头紧", "缓两天"):
            if word in message:
                return "账期" if word in ("手头紧", "缓两天") else word
        return "赊账"
    if intent == "gossip":
        if any(w in message for w in ("谁最", "谁有钱", "哪家")):
            return "谁最有钱"
        name = _person_name(message)
        if name:
            return name
    if intent == "chitchat":
        if any(w in message for w in ("安全", "怪声", "夜里", "天黑", "巡街")):
            return "怪声" if "怪声" in message else "夜里的事"
        if any(w in message for w in ("以后", "将来", "会变", "会成", "往后")):
            return "往后的日子"
        if any(w in message for w in ("怕不怕", "信命")):
            return "加税" if "加税" in message else "命"
        if any(w in message for w in ("刀工", "跟谁学")):
            return "刀工"
        if any(w in message for w in ("这把刀", "把刀", "砍刀", "年头")):
            return "这把刀"
        if "家里" in message:
            return "家里的事"
        if any(w in message for w in ("平时", "除了", "还做什么")):
            return "平日营生"
    if intent == "help":
        if any(w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")):
            return "喝酒"
        if any(w in message for w in ("学门手艺", "建议")):
            return "学手艺"
    text = (message or "").strip()
    text = _VOCATIVE_RE.sub("", text)
    for _ in range(2):
        for word in _LEAD_WORDS:
            if text.startswith(word):
                text = text[len(word):]
                break
    text = re.split(r"[，,。；;！!]", text)[0]
    text = re.sub(r"^(?:你们|我们|他们|你这|我那|他那|这镇子|镇子|镇上|这|那|你|我|他|她)", "", text)
    text = text.strip("　 ，,。；;！!？?…“”\"'")
    for tail in _TAIL_PATTERNS:
        if text.endswith(tail):
            text = text[: -len(tail)]
            break
    text = text.strip("　 ，,。；;！!？?…“”\"'")
    if text.startswith("和"):
        text = text[1:]
    text = re.sub(r"^(?:的)+|(?:的)+$", "", text)
    for raw, label in TOPIC_HINTS:
        if raw in message:
            return label
    if 0 < len(text) <= 10:
        return text
    return TOPIC_DEFAULT.get(intent, "这话")


def _skeleton(intent: str, message: str, turn: int, obj: str) -> str:
    """二维骨架选择：意图类别 + 诉求对象，再用 turn 交替 a/b 变体。

    不同对象不复用同一骨架 id，避免“所有生意问题都答肉价”；同一对象
    连续两轮由 turn 变体轮换，不会给出同一句。
    """

    demand = _demand(message, intent, obj)

    def ab(base: str) -> str:
        return f"{base}.{'ab'[turn % 2]}"

    if intent == "price":
        if demand in (
            "bargain", "bulk", "mix", "extra", "compare",
            "regular", "credit_diff", "goods", "concession",
        ):
            return ab(f"price.{demand}")
        base = {
            "capital": "price.capital",
            "volume": "price.volume",
            "offal": "price.offal",
            "cut": "price.cut",
            "other": "price.other",
            "reason": "price.meat.reason",
        }.get(obj, "price.meat")
        return ab(base)
    if intent == "gossip":
        if obj == "who":
            return "gossip.who"
        if obj in ("impact", "recruit", "passersby"):
            return ab(f"gossip.{obj}")
        if obj == "type":
            return ab("gossip.type")
        if obj == "person":
            base = {
                "exam": "gossip.person.exam",
                "craft": "gossip.person.craft",
                "history_trade": "gossip.person.history.trade",
                "history_post": "gossip.person.history.post",
                "duration": "gossip.person.relation.duration",
                "justice": "gossip.person.justice",
                "acquaintance": "gossip.person.relation.acquaintance",
                "closeness": "gossip.person.relation.closeness",
            }.get(demand, "gossip.person.reputation")
            return ab(base)
        if demand == "whereabouts":
            return ab("gossip.person.whereabouts")
        return ab("gossip.event")
    if intent == "chitchat":
        base = {
            "family": "chitchat.family",
            "sound": "chitchat.sound",
            "safety": "chitchat.safety",
            "future": "chitchat.future",
            "fate": "chitchat.fate",
            "opinion": "chitchat.opinion",
            "craft_origin": "chitchat.craft.origin",
            "craft_knife": "chitchat.craft.knife",
        }.get(obj, "chitchat.daily")
        return ab(base)
    if intent == "help":
        if demand == "invite":
            return "help.invite"
        if demand in (
            "skill", "schedule", "status", "search",
            "referral", "escort", "progress", "alternative",
        ):
            return ab(f"help.{demand}")
        return ab("help")
    if intent == "directions":
        if demand in (
            "landmark", "inn_lamp", "ferry", "avoid_patrol", "well", "temple",
        ):
            return ab(f"directions.{demand}")
        return ab("directions")
    if intent == "credit":
        if demand in (
            "origin", "collateral", "defer", "interest",
            "interest_regular", "repay", "owe", "ledger",
        ):
            return ab(f"credit.{demand}")
        return ab("credit")
    if intent == "rejection":
        if demand in ("borrow", "change_mind", "question", "trust"):
            return ab(f"rejection.{demand}")
        return ab("rejection")
    return ab(intent)


def _pick_quote(voice: dict[str, Any], intent: str, digest: str, last_reply: str) -> str:
    quotes = list(voice["quotes"])
    if not quotes:
        return ""
    hints = QUOTE_HINTS.get(intent, ())
    base = int(digest[:8], 16) % len(quotes)
    ordered = sorted(
        range(len(quotes)),
        key=lambda i: (
            -sum(1 for h in hints if h in quotes[i]),
            len(quotes[i]),
            (i - base) % len(quotes),
        ),
    )
    for i in ordered:
        quote = quotes[i]
        if len(quotes) > 1 and quote in (last_reply or ""):
            continue
        return quote
    return quotes[ordered[0]]


def _trim(body: str, quote: str) -> str:
    """优先保留台词，同时把长度压进 20–60 字。"""
    if len(body) + len(quote) <= 60:
        return body + quote
    if len(body) <= 60:
        return body
    cut = body[:60]
    for mark in ("。", "；", "，", "！"):
        pos = cut.rfind(mark)
        if pos >= 19:
            return cut[: pos + 1]
    return cut


def compose_talk(
    persona_text: str,
    npc_id: str,
    npc_name: str,
    npc_role: str,
    message: str,
    tick: int,
    turn: int = 0,
    last_message: str = "",
    last_reply: str = "",
) -> dict[str, Any]:
    """生成回应与情感增量；上限不在这里做，由 TalkAction 统一 clamp。

    回应是确定性模板：先判意图，再取玩家输入里的具体话题，用第一人称接一句，
    最后按意图挑一条人设台词收尾。`npc_name` 只用于摘要，不写进对白，避免
    “某人听了你的话”这类第三人称旁白。
    """
    message = (message or "").strip()
    voice = extract_persona_voice(persona_text)
    profile = extract_persona_profile(persona_text, npc_role)
    digest = hashlib.sha256(f"{npc_id}|{message}|{tick}|{turn}".encode("utf-8")).hexdigest()
    sent_digest = hashlib.sha256(f"{npc_id}|{message}|{tick}".encode("utf-8")).hexdigest()

    intent = _intent(message)
    obj = _object_type(message, intent)
    topic = _topic(message, intent)
    skeleton = _skeleton(intent, message, turn, obj)
    body = TEMPLATES[skeleton][0].format(
        role=profile["role"], place=profile["place"], topic=topic
    )
    # 台词只挂在“回避”骨架上，避免不同意图复用同一句尾口号。
    quote = _pick_quote(voice, intent, digest, last_reply) if intent == "deflection" else ""
    reply = _trim(body, quote)
    aff, trust = _sentiment(message, sent_digest)
    return {
        "reply": reply,
        "quote": quote,
        "intent": intent,
        "object": obj,
        "demand": _demand(message, intent, obj),
        "skeleton": skeleton,
        "topic": topic,
        "style": voice["style"],
        "reaction": voice["reaction"],
        "affection_delta": aff,
        "trust_delta": trust,
    }
