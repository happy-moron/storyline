You will be given a set of "Voice Profiles" which are written in a custom format which will be parsed by a downstream program.

The profiles are being given to you because they have failed to parse. You'll be given the text of the failing profiles and the parser error messages along with the erroneous script.

Identify any errors is and fix the profiles if you can.

# Voice Profiles Format/Guidelines

All properties are mandatory. The keys/properties should match the English property names as specified below.

Each property should be specified as <property> = "property value" on its own line.
The properties should match the provided order.
The property values are somewhat arbitrary so you have freedom to fill in missing properties, and correct blatantly incorrect properties. Don't modify content unless it is clearly erroneous from a parsing perspective.

## Voice profile property guide

* speaker - 说话人ID（与对话中的ID匹配）
* language - 'en'（英语）或'zh'（中文））
* gender - one of "Male" or "Female"
* age - 年龄或年龄范围（如未知）
* pitch - 基础音域 + 其中任何变化 - 音域（低/中/高）+ 变化方向（如有变化），例如“中音域，因激动而急剧升高”
* pace - 基础语速及任何变化方向
* volume - 说话人的基础响度
* clarity - 词语形成的清晰程度，例如“清晰，每个音节都分明”对比“柔和，略有含混”
* fluency - 表达是否顺畅——犹豫、口吃、填充词——例如“流利，没有犹豫”对比“紧张时口吃，节奏不均匀”
* timbre - 声音本身的音色/质感（例如——有磁性、清脆、沙哑、圆润、甜美、浑厚、有力等）
* emotion - 捕捉说话人核心性格的情感“内核”
* usecase - 说话人的角色/人物，例如“一个缺乏想象力、说话直率的银行家”，“一位和蔼可亲、给人安慰和支持的奶奶”等
* dialogue - 20-25个词的主题性、代表性对话，能体现说话人的性格/情绪

### Sample Correct Output

speaker = 1
lang = "zh"
gender = "男性"
age = "四十五岁左右"
pitch = "低沉男性音区，稳定"
pace = "略快，节奏紧凑"
volume = "洪亮，有很强的穿透力"
clarity = "吐字清晰，措辞有力"
fluency = "流畅，无缝衔接" 
timbre = "浑厚，略带沙哑"
emotion = "庄重，坚定" 
usecase = "一个意志坚强的海盗船长"
dialogue = "你们休想活捉我或我的船。这就是终点了，弟兄们。全员就位！备炮！"

# Your Output Guidelines

## Output the fixed profiles if you can.

If the profiles have clear errors with straightforward fixes, output the fixed profiles.
Output ONLY the properly formatted profiles with no changes or additions except for direct fixes for known/obvious errors.

## Output the word GARBAGE if the script is unfixable

If the profiles has:

* Unclear errors
* Severely truncated or malformed content such that the text is just nonsense
* Non-obvious or strange things going on

Output just a single all caps word: GARBAGE
Don't output anything else, no explanation, just the word GARBAGE.

# Profiles to Fix