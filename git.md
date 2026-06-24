yutiansut/QUANTAXIS (上游)         你的仓库/QUANTAXIS (fork)
│                                    │
master (原主线) ◄── git fetch upstream ───├─ master (同步上游)
│                                    │
│                              └─ my-branch (你的修改)
│                                    │
└── 有新提交时 merge/rebase ──────────┘

具体操作

# 1. 在 GitHub 上 Fork 原仓库
#    打开 https://github.com/yutiansut/QUANTAXIS → 点击右上角 Fork 按钮
#    完成后你会得到: https://github.com/<你的用户名>/QUANTAXIS

# 2. 添加你的 fork 作为推送目标
git remote add myfork https://github.com/<你的用户名>/QUANTAXIS.git

# 3. 添加原仓库为 upstream（用于同步更新）
git remote add upstream https://github.com/yutiansut/QUANTAXIS.git

# 4. 创建分支保存你的修改
git checkout -b my-config

# 5. 提交你的修改
git add -A
git commit -m "feat: MongoDB认证支持 + TDX GBK修复 + 使用文档"

# 6. 推送到你的 fork
git push myfork my-config

# 7. 以后同步上游更新
git checkout master
git fetch upstream
git merge upstream/master          # 或 git rebase upstream/master
git push myfork master

# 8. 将上游更新合并到你的分支
git checkout my-config
git merge master                    # 或 git rebase master