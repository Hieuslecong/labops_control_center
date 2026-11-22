#!/bin/bash
set -eu

original=("$@")
directories=()
unsupported_global=

while (( $# )); do
    case $1 in
        -C)
            if (( $# < 2 )); then break; fi
            directories+=("$2")
            shift 2
            ;;
        -c|--config-env)
            if (( $# < 2 )); then break; fi
            unsupported_global=$1
            shift 2
            ;;
        --no-pager|-P|--paginate|-p|--no-optional-locks|--no-advice)
            shift
            ;;
        --bare|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|--no-replace-objects|--no-lazy-fetch|--config-env=*|--git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--exec-path=*|--attr-source=*)
            unsupported_global=$1
            shift
            ;;
        *) break ;;
    esac
done

if [[ ${1:-} != clone ]]; then
    exec /usr/bin/git "${original[@]}"
fi

if [[ -n $unsupported_global ]]; then
    printf 'LabOps: managed clone does not support global option %s\n' "$unsupported_global" >&2
    exit 1
fi
for directory in "${directories[@]}"; do
    if [[ -z $directory ]]; then continue; fi
    if [[ $directory != /* ]]; then directory=./$directory; fi
    if ! cd -P -- "$directory" 2>/dev/null; then
        printf 'LabOps: cannot use working directory %s\n' "$directory" >&2
        exit 1
    fi
done

shift
options=true
for arg in "$@"; do
    if ! $options; then continue; fi
    case $arg in
        --) options=false ;;
        -b|--branch|--branch=*) ;;
        -*)
            printf 'LabOps: managed clone does not support option %s\n' "$arg" >&2
            exit 1
            ;;
    esac
done

exec /usr/local/bin/aiotgit clone "$@"
