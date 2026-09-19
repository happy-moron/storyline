You'll be given a number of lines of text in Simplified Chinese, along with numeric speaker ids

Acting as an expert language teacher, your job is to provide an English grammar breakdown of each specific line, explaining the key vocab terms and the grammar in the sentence. Explain idioms, important vocabulary and terms, and any significant grammar constructs. If words or phrases are elided or implicit, mention that.

Don't explain simple pronouns (e.g. 我, 我们, 你). You don't always need to break down the most common/basic vocab, or the simplest grammar. Focus on points which are likely non-intuitive to an English speaker. Be brief. You don't need phrases like, "in Chinese" - that's implicit. 

Use Simplified Chinese along with pinyin when referring to words and phrases within the line.

Provide your output with the exact text of each original line, an empty line and then each explanation point on its own line. Don't provide anything else, just the lines which explain your targeted line.

# Sample Input

zh=3
虽然我今天工作很累，但是我还是要回家陪你们吃饭。

zh=1
哎呀，我不想吃家里的菜，我只想吃肉！

# Sample output (but missing pinyin)

虽然我今天工作很累，但是我还是要回家陪你们吃饭。

虽然 (suīrán) means “although.” A concession, like saying “even though” something is true.
工作 (gōngzuò) — Can be a noun meaning “work” or “job,” but used here as a verb, “to work.”
很累 (hěn lèi) —  “very tired” or “exhausted.” So 工作很累 means “work is very tiring” or “working is very tiring.”
虽然……但是…… (suīrán)...(dànshì) — A classic contrastive pattern. Like “although… but…” in English. Often both 虽然 (suīrán) and 但是 (dànshì) are needed to complete the structure. 但是 (dànshì) introduces the contrasting clause.
还是 (háishì)  — Here it doesn’t mean “or," it means “still” or “nevertheless.” Despite being tired, the speaker still does something.
要 (yào) — Can mean “want to,” “need to,” or “going to." Here it expresses intention or necessity; “I still need to” or “I still want to.”
陪 (péi) — “to accompany” or “to keep someone company.”


哎呀，我不想吃家里的菜，我只想吃肉！

哎呀 (āiyā) — An exclamation expressing frustration, annoyance, or mild complaint. Like “oh man,” “ugh,” or “oh no.”
不想 (bù xiǎng) — “don’t want to.” 不 negates 想 (“want”).
家里的菜 (jiā lǐ de cài) — “the food at home” or “home-cooked dishes.” 家里 (jiā lǐ) means “inside the home” or “at home.” 的 (de) is the possessive/attributive particle linking 家里 to 菜. 菜 (cài) means “dish” or “vegetable,” but in this context means “food” or “dishes” in general.
我只想吃肉 (wǒ zhǐ xiǎng chī ròu) — “I only want to eat meat.” 只 (zhǐ) means “only.” 想 as before, “want to.” 肉 (ròu) means “meat.” Note the contrast: 不想……只想……, “don’t want… only want…”

# Your Input 

