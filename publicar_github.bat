@echo off
REM Nuria - publica esta pasta no repositorio PRIVADO https://github.com/adryell08/ayra
REM Seguro para rodar de novo: so envia o que mudou. Nunca envia .env, *.local.json nem logs.
chcp 65001 >nul
cd /d "%~dp0"
set REPO=https://github.com/adryell08/ayra.git
where git >nul 2>nul || (echo Git nao encontrado. Instale em git-scm.com e tente de novo. & pause & exit /b 1)

if not exist .git (
  git init -b main || (pause & exit /b 1)
)
git config user.name >nul 2>nul || (
  set /p NOME=Seu nome para os commits: 
  call git config user.name "%%NOME%%"
)
git config user.email >nul 2>nul || (
  set /p EMAIL=Seu e-mail do GitHub: 
  call git config user.email "%%EMAIL%%"
)
git remote get-url origin >nul 2>nul && git remote set-url origin %REPO% || git remote add origin %REPO%

git add -A
REM Trava de seguranca: aborta se algum segredo ou arquivo local entrou na lista.
git diff --cached --name-only | findstr /I /R "^\.env$ \.local\.json$ ^logs/" >nul && (
  echo ERRO: um arquivo sigiloso entraria no commit. Nada foi enviado.
  git diff --cached --name-only | findstr /I /R "^\.env$ \.local\.json$ ^logs/"
  git reset -q
  pause & exit /b 1
)
git grep --cached -I -n -E "sk-(proj|svcacct|admin)?-?[A-Za-z0-9_-]{30,}" -- . ":!test_*.py" >nul 2>nul && (
  echo ERRO: algo parecido com uma chave da OpenAI esta no codigo. Nada foi enviado:
  git grep --cached -I -n -E "sk-(proj|svcacct|admin)?-?[A-Za-z0-9_-]{30,}" -- . ":!test_*.py"
  git reset -q
  pause & exit /b 1
)

git diff --cached --quiet && (echo Nada novo para registrar.) || (
  git commit -q -F publicar_github_msg.txt || (pause & exit /b 1)
)

REM Se o repositorio foi criado com README, junta os historicos mantendo os arquivos daqui.
git fetch -q origin 2>nul
git rev-parse -q --verify origin/main >nul && (
  git merge -q --allow-unrelated-histories -X ours -m "Junta o README inicial do GitHub" origin/main 2>nul
)
echo Enviando para %REPO% (o Windows pode abrir o login do GitHub no navegador)...
git push -u origin main || (echo Falhou o envio. Confira o login do GitHub e se o repositorio existe. & pause & exit /b 1)
echo.
echo Pronto: https://github.com/adryell08/ayra
pause
