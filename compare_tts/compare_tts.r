library(tidyverse)
library(glmnet)
library(survival)
library(jsonlite)

### Helper functions ###
script_dir = tryCatch(
  dirname(normalizePath(sys.frame(1)$ofile)),
  error = function(e) "/mnt/c/Research/textual-time-series-for-sepsis/compare_tts/"
)

`%||%` <- function(x, y) if (is.null(x)) y else x

get_file_paths <- function(loc, suffix=".*", dir.append=T) {
    tibble(files = list.files(loc)) %>%
    mutate(paths = ifelse(rep(dir.append,nrow(.)), paste0(loc,files), files)) %>%
    filter(str_detect(paths, paste0(suffix, "$"))) %>%
    .[["paths"]]
}

ecdf_auc2 <- function(df, def=0, upper=upper_limit) {
    df %>%
    bind_rows(tibble(field=upper)) %>%
    arrange(field) %>% 
    mutate(field = pmin(field, upper)) %>%
    mutate(
        field2 = lead(field, default=def),
        nth = (1:n())/(n()-1),
        dx = field2 - field,
        box = dx*nth 
    ) %>%
    slice(-n()) %>%
    summarise(auc = sum(box)/(upper-0)) %>% t() %>% c()
}

### Config and initialization functions ###

validate_config <- function(config) {
  # Check required fields
  required_fields <- c("man_loc", "ref_locs", "pilot_names")
  missing_fields <- setdiff(required_fields, names(config))
  if (length(missing_fields) > 0) {
    stop(paste("Missing required config fields:", paste(missing_fields, collapse=", ")))
  }
  
  # Check ref_locs and pilot_names have same length
  if (length(config$ref_locs) != length(config$pilot_names)) {
    stop("ref_locs and pilot_names must have the same length")
  }
  
  # Check directories exist
  check_directory_exists(config$man_loc, "Manual annotation directory")
  sapply(config$ref_locs, function(x) check_directory_exists(x, "Reference directory"))
  
  # Check distance parameters
  if (!is.null(config$distance)) {
    if (!config$distance$method %in% c("embedding_cosine", "string_similarity")) {
      warning(paste("Unknown distance method:", config$distance$method, 
                   "- using default 'embedding_cosine'"))
      config$distance$method <- "embedding_cosine"
    }
    
    if (!is.numeric(config$distance$threshold) || 
        config$distance$threshold < 0 || config$distance$threshold > 1) {
      warning("Distance threshold must be between 0 and 1 - using default 0.1")
      config$distance$threshold <- 0.1
    }
  }

  if (!is.null(config$use_lap) && !is.logical(config$use_lap)) {
    stop("use_lap must be true/false")
  }
  
  return(config)
}

check_directory_exists <- function(path, description) {
  if (!dir.exists(path)) {
    stop(paste(description, "not found at:", path))
  }
  
  # Check if directory contains .bsv files
  # bsv_files <- list.files(path, pattern = "\\.bsv$")
  # if (length(bsv_files) == 0) {
  #   warning(paste("No .bsv files found in", description, "at:", path))
  # }
}

### Update read_config to include validation ###

read_config <- function(config_path) {
  if (!file.exists(config_path)) {
    stop(paste("Config file not found at:", config_path))
  }
  
  config <- fromJSON(config_path)
  
  # Set defaults if not specified
  if (is.null(config$out)) {
    config$out <- list(
      relfolder = "matches/",
      folder = paste0(tempdir(), "/"),
      event_out_locs = tempdir()
    )
  }
  
  if (is.null(config$distance)) {
    config$distance <- list(
      method = "embedding_cosine",
      threshold = 0.1
    )
  }

  if (is.null(config$approach)) {
    config$approach <- "strcmp"
    # use config$approach = "featurized" if you want to use charpos
  }
  
  if (is.null(config$man_suffix)) {
    config$man_suffix <- ".bsv"
  }
  if (is.null(config$ref_suffix)) {
    config$ref_suffix <- ".bsv"
  }

  # Default header flag from file suffix: .csv files always have headers,
  # .bsv/.bsv.gz files do not.  Explicit config values always take precedence.
  if (is.null(config$man_header)) {
    config$man_header <- tolower(config$man_suffix) %in% c(".csv", ".csv.gz")
    message(paste("Defaulted man_header from suffix:", config$man_header))
  }
  if (is.null(config$ref_header)) {
    config$ref_header <- tolower(config$ref_suffix) %in% c(".csv", ".csv.gz")
    message(paste("Defaulted ref_header from suffix:", config$ref_header))
  }

  # Add upper_limit default (1 year in minutes)
  if (is.null(config$upper_limit)) {
    config$upper_limit <- log(60*24*365.26)
  }

  if (is.null(config$use_lap)) {
    config$use_lap <- TRUE
  }
  
  # Validate config
  config <- validate_config(config)
  
  return(config)
}


initialize_sources <- function(config) {
  tibble(locs = c(config$man_loc, config$ref_locs), 
         loctype = c("reference", rep("pilot", length(config$ref_locs))),
         loc.names = c("manual", config$pilot_names),
         loc.suffix = c(config$man_suffix, rep(config$ref_suffix, length(config$ref_locs)))
  ) %>%
  mutate(files = map2(locs, loc.suffix, ~ get_file_paths(.x, .y))) %>%
  mutate(wo_paths = map2(locs, loc.suffix, ~ get_file_paths(.x, .y, dir.append=F))) %>% 
  unnest(everything()) %>%
  mutate(bns = map2_chr(wo_paths, loc.suffix, ~ str_replace(.x, paste0(.y,"$"), "")))
}

### Event matching functions ###

perform_event_matching <- function(sources, config) {
  # source("compare_tts/helper_comparer.r")
  # source("/data/weissjc/lns/tta/compare_tts/helper_comparer.r")
  source(file.path(script_dir, "helper_comparer.r"))
  match.tbl <- sources %>% 
    filter(loctype=="reference") %>% 
    select(-loc.names) %>%
    inner_join(
      sources %>% filter(loctype=="pilot") %>%
      select(bns, pilot.wo_paths=wo_paths, pilot.files = files, pilot.locs = locs, pilot.names=loc.names),
      by = c("bns")
    ) %>%
    select(-loctype, -locs) %>%
    rename(common.bns=bns)
    # mutate(referent.events = map(files, ~ 
    #        read_delim(.x, delim="|", col_names=F,col_types="c", trim_ws=T) %>% 
    #        select(event=1, time=2) %>% .[["event"]])) %>%
    # mutate(pilot.events = map(pilot.files, ~ 
    #        read_delim(.x, delim="|", col_names=F,col_types="c", trim_ws=T) %>% 
    #        select(event=1, time=2) %>% .[["event"]])) %>%
    # mutate(event.match = map2(referent.events, pilot.events, 
    #   ~ get_match_table(.x, .y, method = config$distance$method) %>%
    #   group_by(v1) %>% 
    #   mutate(rowid=1:n()) %>% # label them by unique rowid per unique ref event
    #   ungroup()
    # ))
  man_delim = ifelse(str_detect(config$man_suffix, c(".bsv$",".bsv.gz$")) %>% any, "|",",")
  pilot_delim = ifelse(str_detect(config$ref_suffix, c(".bsv$",".bsv.gz$")) %>% any, "|",",")
  # Helper: read a file and return a tibble of extra columns (beyond col 1+2),
  # prefixed with <prefix>., plus an idx key matching the row numbers used for
  # matching.  Returns NULL when there are no extra columns.
  # IMPORTANT: must use the same read parameters (delim, col_names) as the
  # main read so that row numbers are identical.
  extract_extra_cols <- function(df_full, prefix) {
    if (ncol(df_full) <= 2) return(NULL)
    extra <- df_full[, 3:ncol(df_full), drop = FALSE]
    names(extra) <- paste0(prefix, ".", names(extra))
    extra[["idx"]] <- seq_len(nrow(extra))
    extra
  }

  if(config$approach == "strcmp") {
    match.tbl = match.tbl %>%
      mutate(ref_full = map(files, ~
            read_delim(.x, delim=man_delim, col_names=config$man_header, col_types="c", trim_ws=T)
            )) %>%
      mutate(pilot_full = map(pilot.files, ~
            read_delim(.x, delim=pilot_delim, col_names=config$ref_header, col_types="c", trim_ws=T)
            )) %>%
      mutate(referent.data = map(ref_full, ~
            .x %>% select(referent.events=1, referent.time=2) %>% mutate(idx = 1:n())
            )) %>%
      mutate(pilot.data = map(pilot_full, ~
            .x %>% select(pilot.events=1, pilot.time=2) %>% mutate(idx = 1:n())
            )) %>%
      mutate(ref_extra   = map(ref_full,   ~ extract_extra_cols(.x, "ref"))) %>%
      mutate(pilot_extra = map(pilot_full, ~ extract_extra_cols(.x, "pilot")))
  } else if (config$approach == "featurized") {
    match.tbl = match.tbl %>%
      mutate(ref_full = map(files, ~
            read_delim(.x, delim=man_delim, col_names=config$man_header, col_types="c", trim_ws=T)
            )) %>%
      mutate(pilot_full = map(pilot.files, ~
            read_delim(.x, delim=pilot_delim, col_names=config$ref_header, col_types="c", trim_ws=T)
            )) %>%
      mutate(referent.data = map(ref_full, ~
            .x %>% select(referent.events=1, referent.time=2, char_pos, char_pos_ub) %>% mutate(idx = 1:n())
            )) %>%
      mutate(pilot.data = map(pilot_full, ~
            .x %>% select(pilot.events=4, pilot.time=5, char_pos, char_pos_ub) %>% mutate(idx = 1:n())
            )) %>%
      mutate(ref_extra   = map(ref_full,   ~ extract_extra_cols(.x, "ref"))) %>%
      mutate(pilot_extra = map(pilot_full, ~ extract_extra_cols(.x, "pilot")))
    # browser()
  } else {
    stop(paste("this config$approach is unimplemented:", config$approach))
  }
  match.tbl = match.tbl %>%
    mutate(event.match = map2(referent.data, pilot.data, 
      ~ get_match_table_wtimes(.x, .y, approach=config$approach, method = config$distance$method, use_lap=config$use_lap, rowids=T)
      # group_by(v1) %>%  ## TODO INPSECT, rowid is a proxy for mention uid4, but is re-ordering consistent? it is established here. but is it joined on?
      # mutate(rowid=1:n()) %>% # label them by unique rowid per unique ref event
      # ungroup()
    ))
  
  # Create output directories (centralized under config$out$folder)
  # Matches will be saved to: <out.folder>/<out.relfolder>/<pilot.name>/best_matchesYYYY-MM-DD.csv
  match.tbl %>%
    select(pilot.names) %>%
    distinct() %>%
    mutate(out_dir = file.path(config$out$folder, config$out$relfolder, pilot.names)) %>%
    mutate(complete = map(out_dir, ~ dir.create(.x, recursive = TRUE, showWarnings = FALSE)))

  # Save matches — join extra columns (already extracted during the read above)
  # onto event.match before writing.  ref_extra keys on idx; pilot_extra on match.idx.
  match.tbl %>%
    select(pilot.names, common.bns, files, pilot.files, event.match,
           ref_extra, pilot_extra) %>%
    mutate(
      event.match = pmap(
        list(event.match, ref_extra, pilot_extra),
        function(em, re, pe) {
          out <- em
          if (!is.null(re)) {
            out <- left_join(out, re, by = "idx")
          }
          if (!is.null(pe)) {
            names(pe)[names(pe) == "idx"] <- "match.idx"
            out <- left_join(out, pe, by = "match.idx")
          }
          out
        }
      )
    ) %>%
    select(pilot.names, common.bns, files, pilot.files, event.match) %>%
    unnest(everything()) %>%
    nest(data = -pilot.names) %>%
    mutate(complete = map2(pilot.names, data, ~ .y %>% write_csv(
      file.path(config$out$folder, config$out$relfolder, .x,
                paste0("best_matches", as.character(lubridate::today()), ".csv"))
    )))
return(match.tbl)
}

load_saved_matches <- function(sources, config) {
  loaded.match.tbl <- tibble(pilot.locs = config$event_match_files) %>%
    mutate(data = map(pilot.locs, ~ read_csv(.x))) %>%
    unnest(everything())

  # Backward compatibility: add 'keep' column if it doesn't exist
  if (!"keep" %in% names(loaded.match.tbl)) {
    loaded.match.tbl <- loaded.match.tbl %>%
      mutate(keep = error.rate < config$distance$threshold)
  }

  match.tbl <- sources %>% 
    filter(loctype=="reference") %>% 
    select(-loc.names) %>%
    inner_join(
      sources %>% filter(loctype=="pilot") %>%
      select(wo_paths, pilot.files = files, pilot.locs = locs, pilot.names=loc.names),
      by = c("wo_paths")
    ) %>%
    select(-loctype, -locs) %>%
    rename(common.bns=bns) %>%
    mutate(
      common.files.match = str_replace_all(common.bns, ".(b|c)sv",""),
      pilot.files.match = str_replace_all(pilot.files, ".(b|c)sv",""),
      files.match = str_replace_all(files, ".(b|c)sv","")
    ) %>%
    inner_join(
      loaded.match.tbl %>% select(-pilot.locs) %>%
      mutate(
        common.files.match = str_replace_all(common.bns, ".(b|c)sv",""),
        pilot.files.match = str_replace_all(pilot.files, ".(b|c)sv",""),
        files.match = str_replace_all(files, ".(b|c)sv","")
      ) %>% select(-common.bns,-pilot.files,-files),
      by=c("common.files.match","pilot.files.match","files.match")
    ) %>% 
    nest(event.match = c(v1,v2,error.rate,keep,idx,match.idx)) #used to be rowid.  /breaks back-compatibility
  # browser()
  return(match.tbl)
}

### Analysis functions ###

prepare_match_data <- function(match.tbl, config) {
  # browser()
  man_delim = ifelse(str_detect(config$man_suffix, c(".bsv$",".bsv.gz$")) %>% any, "|",",")
  pilot_delim = ifelse(str_detect(config$ref_suffix, c(".bsv$",".bsv.gz$")) %>% any, "|",",")
  if(config$approach == "strcmp") {
    result = match.tbl %>%
      mutate(man.tuples = map(files, ~ read_delim(paste0(.x),
                                        # skip_empty_rows = F, 
                                        delim=man_delim,
                                        col_types=cols("c","c"), 
                                        col_names=config$man_header,
                                        trim_ws=T) %>% 
                                        select(event=1,time=2) %>%
                                        mutate(idx = 1:n())
                                        # group_by(event) %>% # ,time) %>% # uid4 is for an event not an event-time tuple
                                        # mutate(rowid=1:n()) %>% 
                                        # ungroup()
                            )) %>%
      mutate(man.join.match = map2(man.tuples, event.match,  # 1 to 1 (recursive match) with unmatched ref events
        ~ inner_join(.x, .y, by=c("event"="v1","idx"="idx")))
        # ~ inner_join(.x, .y, by=c("event"="v1","rowid"="rowid")))
      ) %>%
      mutate(pilot.tuples = map(pilot.files, ~ read_delim(paste0(.x), delim=pilot_delim,
                                        # skip_empty_rows = F,
                                        col_types=cols("c","c"),
                                        col_names=config$ref_header,
                                        trim_ws=T) %>% 
                                        select(event=1,time=2) %>%
                                        mutate(idx = 1:n())
                                        # group_by(event) %>% # ,time) %>% # uid4 is for an event not an event-time tuple
                                        # mutate(rowid=1:n()) %>% 
                                        # ungroup()
                                )) %>%
      mutate(man.join.match.join.pilot = map2(pilot.tuples, man.join.match,
        ~ full_join(.x %>%  # remember to remove the set difference as necessary, and to deduplicate
        # possibilities: multiple events (diff times), duplications, no match?
          mutate(time = str_replace_all(time, "\\\\","")) %>%
          rename(time.pilot=time, event.pilot=event), .y, by=c("event.pilot"="v2","idx"="match.idx")))
          # rename(time.pilot=time, event.pilot=event), .y, by=c("event.pilot"="v2","rowid"="rowid")))
      )
    return(result)
  } else if(config$approach == "featurized") {
    result = match.tbl %>%
      mutate(man.tuples = map(files, ~ read_delim(paste0(.x),
                                        # skip_empty_rows = F, 
                                        delim=man_delim,
                                        col_types="c", 
                                        col_names=config$man_header,
                                        trim_ws=T) %>% 
                                        select(event=1,time=2, char_pos, char_pos_ub) %>%
                                        mutate(idx=1:n())
                                        # group_by(event) %>% # ,time,char_pos,char_pos_ub) %>% # make (implicit) unique uid4 by matching on (mention, sub_rowid)
                                        # mutate(rowid=1:n()) %>% 
                                        # ungroup()
                            )) %>%
      mutate(man.join.match = map2(man.tuples, event.match,  # 1 to 1 (recursive match) with unmatched ref events
        ~ inner_join(.x, .y, by=c("event"="v1","idx"="idx")))
        # ~ inner_join(.x, .y, by=c("event"="v1","rowid"="rowid")))
      ) %>%
      mutate(pilot.tuples = map(pilot.files, ~ read_delim(paste0(.x), delim=pilot_delim,
                                        # skip_empty_rows = F,
                                        col_types="c",
                                        col_names=config$ref_header,
                                        trim_ws=T) %>% 
                                        select(event=4,time=5, pilot.char_pos=char_pos, pilot.char_pos_ub=char_pos_ub) %>%
                                        mutate(idx=1:n())
                                        # group_by(event) %>% # ,time) %>% # again, should be by (mention, sub_rowid)
                                        # mutate(rowid=1:n()) %>% 
                                        # ungroup()
                                )) %>%
      mutate(man.join.match.join.pilot = map2(pilot.tuples, man.join.match,
        ~ full_join(.x %>%  # remember to remove the set difference as necessary, and to deduplicate
        # possibilities: multiple events (diff times), duplications, no match?
          mutate(time = str_replace_all(time, "\\\\","")) %>%
          rename(time.pilot=time, event.pilot=event), .y, by=c("event.pilot"="v2","idx"="match.idx")))
          # rename(time.pilot=time, event.pilot=event), .y, by=c("event.pilot"="v2","rowid"="rowid")))
      )
    return(result)
  } else {
    stop("Config approach not implemented")
  }
}

generate_match_stats <- function(match.tbl, threshold) {
  # browser()
  match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match) %>%
    # select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>% # not this because of multiple joins (possible row duplication)
    unnest(everything()) %>%
    mutate(keep=error.rate < threshold, threshold = threshold) %>% 
    rename(Version=pilot.names) %>%
    mutate(error.rate = ifelse(is.na(error.rate),Inf, error.rate)) %>%
    group_by(Version) %>%
    summarise(`Match Rate`=mean(error.rate<threshold), count=n())
}

my_cindex = function(pred, truth, ties_value=0.5, nonzero.threshold=1e-5) {
  # pred_ties = 0.5 means you get some credit for tied preds when there is a true ordering (matches glmnet::Cindex)
  # set pred_ties to 0 if you do not want to give credit for that (encourage correct ordering rather than vascillation)
  expand_grid(tibble(pred, truth), tibble(pred2=pred, truth2=truth)) %>% 
    # filter(truth!=truth2) %>% 
    filter(abs(truth - truth2) > nonzero.threshold) %>% 
    mutate(
      result=ifelse(
        (truth-truth2)*(pred-pred2)>0,
        1,
        # ifelse((truth-truth2)*(pred-pred2)==0,ties_value,0)
        ifelse((truth-truth2)*(pred-pred2)>-nonzero.threshold,ties_value,0)
      )
    ) %>% summarize(Cindex=mean(result)) %>% .[[1,1]]
  # expand_grid(tibble(pred, truth), tibble(pred2=pred, truth2=truth)) %>% filter(truth!=truth2) %>% mutate(result=(truth-truth2)*(pred-pred2)>1e-50) %>% summarize(Cindex=mean(result)) %>% .[[1,1]]
}

calculate_time_parse_rate <- function(match.tbl, threshold) {
  # browser()
  match.tbl %>% 
  select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>%
  unnest(everything()) %>%
  filter(!is.na(event.pilot)) %>%
  filter(!is.na(as.numeric(time))) %>%
  filter(error.rate<threshold) %>%
  mutate(time.pilot = as.numeric(time.pilot)) %>% 
  # mutate(time.pilot = ifelse(is.na(time.pilot),1e8,time.pilot)) %>%
  group_by(Version = pilot.names) %>%
  summarise(parse_rate = sum(!is.na(time.pilot))/n()) %>%
  ungroup()
}

calculate_concordance <- function(match.tbl, threshold, na.times="Inf") {
  # browser()
  if(na.times == "remove") {
    match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>%
    unnest(everything()) %>%
    filter(!is.na(event.pilot)) %>%
    mutate(time=as.numeric(time)) %>%
    mutate(time.pilot = as.numeric(time.pilot)) %>% 
    # mutate(time.pilot = ifelse(is.na(time.pilot),1e8,time.pilot)) %>%
    filter(!is.na(time.pilot)) %>%
    filter(error.rate<threshold) %>%
    nest(data=-c(common.bns, pilot.names)) %>%
    mutate(
      concordance = map_dbl(data,
        # ~ 1 - Cindex(pred=.x$time.pilot, y=Surv(.x$time, rep(1,nrow(.x))))
        ~ my_cindex(pred=.x$time.pilot, truth=.x$time, ties_value=0.5)
      )
    ) %>% 
    select(-data) %>% 
    filter(!is.na(concordance)) %>%
    group_by(pilot.names) %>%
    summarise(
      Concordance = median(concordance),
      C75 = quantile(concordance, .75),
      C25 = quantile(concordance, .25)
    )
  } else if(na.times == "Inf") {
  match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>%
    unnest(everything()) %>%
    filter(!is.na(event.pilot)) %>%
    mutate(time=as.numeric(time)) %>%
    mutate(time.pilot = as.numeric(time.pilot)) %>% 
    mutate(time.pilot = ifelse(is.na(time.pilot),1e8,time.pilot)) %>%
    filter(error.rate<threshold) %>%
    nest(data=-c(common.bns, pilot.names)) %>%
    mutate(
      concordance = map_dbl(data,
        # ~ 1 - Cindex(pred=.x$time.pilot, y=Surv(.x$time, rep(1,nrow(.x))))
        ~ my_cindex(pred=.x$time.pilot, truth=.x$time, ties_value=0.5)
      )
    ) %>% 
    select(-data) %>% 
    filter(!is.na(concordance)) %>%
    group_by(pilot.names) %>%
    summarise(
      Concordance = median(concordance),
      C75 = quantile(concordance, .75),
      C25 = quantile(concordance, .25)
    )
  }
}

plot_timetime = function(match.tbl) {
  match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>%
    unnest(everything()) %>%
    mutate(time=as.numeric(time)) %>%
        filter(!is.na(event.pilot)) %>%
    mutate(time.pilot = as.numeric(time.pilot)) %>% 
    mutate(time.pilot = ifelse(is.na(time.pilot),1e8,time.pilot)) %>%
    mutate(ltime = ifelse(time>0, log1p(time), -log1p(-time))) %>%
    mutate(ltime.pilot = ifelse(time.pilot>0, log1p(time.pilot), -log1p(-time.pilot))) %>%
    mutate(Version=pilot.names) %>%
    ggplot(data=.) +
    # geom_point(aes(x=ltime, y=ltime.pilot,color=common.files)) +
    geom_point(aes(x=ltime, y=ltime.pilot,color=Version), alpha=0.5) +
    geom_abline(slope=1,intercept=0)+
    theme_minimal() +
    theme(panel.grid.minor = element_blank(),
        legend.position=c(0.3,0.7),
        legend.box.background = element_rect(color="#f3f3f3", fill="#f3f3f3", size=0)
    ) +
    xlab("True time") + 
    ylab("Predicted time")
}

calculate_time_discrepancy <- function(match.tbl, threshold, upper_limit) {
  # match.tbl %>% 
  #   select(common.files, pilot.files, pilot.names, man.join.match.join.pilot) %>%
  #   unnest(everything()) %>%
  #   mutate(time=as.numeric(time)) %>% 
  #   mutate(time.pilot = as.numeric(time.pilot)) %>%
  #   mutate(keep=error.rate < threshold, threshold = threshold) %>%
  #   rename(Version=pilot.names) %>% 
  #   filter(keep) %>%
  #   mutate(ae = abs(as.numeric(time.pilot)-as.numeric(time))) %>%
  #   mutate(lae = ifelse(is.na(ae), exp(upper_limit),ae+1)) %>%
  #   select(Version, lae) %>%
  #   nest(data=-Version) %>%
  #   mutate(auc = map_dbl(data,
  #     ~ .x %>% select(field=lae) %>% mutate(field=log(field)) %>%
  #       ecdf_auc2(upper=upper_limit)
  #   )) %>% unnest(everything())

  match.tbl %>% select(common.bns, pilot.files, pilot.names, man.join.match.join.pilot) %>%
    unnest(everything()) %>%
    # mutate(dur = as.duration(ifelse(time=="0","0 seconds",time)) %>% as.numeric("hours")) %>%
    # mutate(time = ifelse(str_starts(str_trim(time), "-"), -dur, dur)) %>%
    mutate(time=as.numeric(time)) %>% 
    # distinct() %>%
    # select(-dur) %>%
    mutate(time.pilot = as.numeric(time.pilot)) %>%
    filter(!is.na(time.pilot), !is.na(event)) %>%
    mutate(keep=error.rate < threshold, threshold = threshold) %>%
    rename(Version=pilot.names) %>% 
    filter(keep) %>% #nest(data=-file.name)
    mutate(ae = abs(as.numeric(time.pilot)-as.numeric(time))) %>%
    mutate(lae = ifelse(is.na(ae), exp(upper_limit)+1,ae+1)) %>%    select(Version, lae) %>%
    nest(data=-Version) %>%
    mutate(auc = map_dbl(data,
        ~ .x %>% select(field=lae) %>% mutate(field=log(field)) %>%
          ecdf_auc2(upper=upper_limit)
      ))
}

### Plotting functions ###

plot_event_match <- function(match.tbl, threshold, max.error.rate) {
  match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match) %>%
    unnest(everything()) %>%
    mutate(keep=error.rate < threshold, threshold = threshold) %>%
    rename(Version=pilot.names) %>%
    mutate(Version = fct_relevel(Version, sort(levels(Version)))) %>%
    ggplot(data = ., aes(x=ifelse(is.na(error.rate) | is.infinite(error.rate),
                                 max(error.rate[!is.infinite(error.rate)],na.rm=T)+0.01, 
                                 error.rate),
                         color=Version)) +
    stat_ecdf() + 
    geom_segment(aes(x = threshold, xend=threshold, y=0, yend=1), 
                color="grey", alpha=0.5) + 
    xlab("Error rate") +
    scale_y_continuous(n.breaks=10) + 
    theme_minimal() + 
    scale_color_manual(values = c("#000000","#E69F00","#56B4E9","#009E73",
                                "#F0E442","#0072B2","#D55E00","#CC79A7", "#AA2200"))+
    scale_fill_manual(values = c("#000000","#E69F00","#56B4E9","#009E73",
                               "#F0E442","#0072B2","#D55E00","#CC79A7", "#AA2200"))+
    theme(panel.grid.minor = element_blank(), 
        legend.position=c(0.7,0.1),
        legend.box.background = element_rect(color="#f3f3f3", fill="#f3f3f3", size=2),
        legend.text = element_text(size = 8),
        legend.title=element_text(size=10)
    ) +
    coord_cartesian(xlim=c(0, max.error.rate)) +
    ylab("Cumulative density") + 
    xlab("Cosine distance")
}

plot_concordance <- function(concordance_results) {
  concordance_results %>%
    mutate(Version = fct_relevel(pilot.names, sort(levels(pilot.names)))) %>%
    ggplot(data=.) + 
    geom_boxplot(aes(y=Concordance, x=Version, fill=Version), alpha=0.5) + 
    xlab("") + 
    ylab("Concordance") +
    scale_fill_manual(values = c("#000000","#E69F00","#56B4E9","#009E73",
                               "#F0E442","#0072B2","#D55E00","#CC79A7", "#AA2200"))+
    scale_y_continuous(n.breaks=10,limits = c(0.5,1)) +
    theme_minimal() +
    theme(axis.text.x = element_text(angle = 90, vjust = 0.5, hjust=1),    
        legend.position = "none"
    )
}

plot_time_discrepancy <- function(time_discrepancy_data) {
  time_discrepancy_data %>% unnest(everything()) %>%
    mutate(Version = fct_relevel(Version, sort(levels(Version)))) %>%
    ggplot(data=., aes(x=lae, color=Version)) + 
    stat_ecdf(alpha=0.9) +
    geom_histogram(alpha=0.15, aes(y=0.2*after_stat(density), fill=Version), 
                  position="identity",color=NA, binwidth=0.2) +
    scale_color_manual(values = c("#000000","#E69F00","#56B4E9","#009E73",
                                "#F0E442","#0072B2","#D55E00","#CC79A7", "#AA2200"))+
    scale_fill_manual(values = c("#000000","#E69F00","#56B4E9","#009E73",
                               "#F0E442","#0072B2","#D55E00","#CC79A7", "#AA2200"))+
    scale_x_log10(breaks=1+c(0,1, 24, 24*7, 24*365.25), 
                 labels=c("exact","hour","day","week","year"),
                 guide=guide_axis(angle=90)) +
    scale_y_continuous(n.breaks=10) +
    geom_vline(xintercept = 1+c(0,1, 24, 24*7, 24*365.25), alpha=0.2) +
    theme_minimal() +
    theme(panel.grid.minor = element_blank(),
        legend.position=c(0.7,0.3),
        legend.box.background = element_rect(color="#f3f3f3", fill="#f3f3f3", size=0)
    ) +
    xlab("Time difference (log)") + 
    ylab("Cumulative probability")
}

### Main function ###

compare_tts <- function(config_path) {
  # Read and validate config
  tryCatch({
    config <- read_config(config_path)
    message("Configuration validated successfully")
    message(paste("Found", length(config$ref_locs), "reference locations"))
    message(paste("Using distance method:", config$distance$method))
    message(paste("Using distance threshold:", config$distance$threshold))
    message(paste("Using approach:", config$approach))
    message(paste("Using manual suffix (man_suffix):", config$man_suffix))
    message(paste("Using pilot suffix (ref_suffix):", config$ref_suffix))

    # --- ensure output directories exist ---
    dir.create(config$out$folder, recursive = TRUE, showWarnings = FALSE)
    dir.create(file.path(config$out$folder, config$out$relfolder),
              recursive = TRUE, showWarnings = FALSE)
    dir.create(config$out$event_out_locs, recursive = TRUE, showWarnings = FALSE)
  }, error = function(e) {
    stop(paste("Config validation failed:", e$message))
  })
  # Initialize sources
  sources <- initialize_sources(config)
  
  # Perform or load event matching
  if (config$event_match) {
    match.tbl <- perform_event_matching(sources, config)
  } else {
    match.tbl <- load_saved_matches(sources, config)
  }
  
  # Prepare match data
  match.tbl <- prepare_match_data(match.tbl, config)
  
  # Generate statistics
  match_stats <- generate_match_stats(match.tbl, config$distance$threshold)
  time_parse_rate <- calculate_time_parse_rate(match.tbl, config$distance$threshold)
  concordance_results <- calculate_concordance(match.tbl, config$distance$threshold, na.times="remove")
  time_discrepancy <- calculate_time_discrepancy(match.tbl, config$distance$threshold, log(60*24*365.26))
  # browser()

  # Create plots
  max.error.rate <- match.tbl %>% 
    select(common.bns, pilot.files, pilot.names, man.join.match) %>%
    unnest(everything()) %>% 
    select(error.rate) %>% 
    filter(is.finite(error.rate)) %>% 
    max()
  
  # Create output directory if it doesn't exist
  if(!dir.exists(paste0(config$out$folder, config$out$relfolder))) { 
    dir.create(config$out$folder, recursive = TRUE, showWarnings = FALSE)
    dir.create(file.path(config$out$folder, config$out$relfolder),
              recursive = TRUE, showWarnings = FALSE)
  }
  
  # Ensure output folder exists
  dir.create(config$out$folder, recursive = TRUE, showWarnings = FALSE)

  # Save plots to PDF
  pdf(paste0(config$out$folder,"figures.pdf"), width=3.2, height=6)
  
  print(plot_event_match(match.tbl, config$distance$threshold, max.error.rate))
  print(plot_concordance(concordance_results))
  print(plot_time_discrepancy(time_discrepancy))
  print(plot_timetime(match.tbl))
  
  dev.off()

  # ---- Save summary metrics CSV next to figures.pdf ----
  summary_tbl <- match_stats %>%
    rename(pilot.names = Version,
           match_rate  = `Match Rate`) %>%
    select(pilot.names, match_rate, count) %>%
    full_join(
      time_parse_rate %>%
        rename(pilot.names = Version) %>%
        select(pilot.names, parse_rate),
      by = "pilot.names"
    ) %>%
    full_join(
      concordance_results %>%
        select(pilot.names,
               concordance = Concordance,
               c25 = C25,
               c75 = C75),
      by = "pilot.names"
    ) %>%
    full_join(
      time_discrepancy %>%
        rename(pilot.names = Version) %>%
        select(pilot.names, auc),
      by = "pilot.names"
    )

  # Optional: enforce config order (recommended)
  if (!is.null(config$pilot_names)) {
    summary_tbl <- summary_tbl %>%
      mutate(pilot.names = factor(pilot.names, levels = config$pilot_names)) %>%
      arrange(pilot.names) %>%
      mutate(pilot.names = as.character(pilot.names))
  } else {
    summary_tbl <- summary_tbl %>% arrange(pilot.names)
  }

  # Final column order you wanted
  summary_tbl <- summary_tbl %>%
    select(pilot.names, match_rate, count, parse_rate, concordance, c25, c75, auc)

  out_csv <- file.path(config$out$folder, "summary_metrics.csv")
  readr::write_csv(summary_tbl, out_csv)
  message("Wrote summary metrics to: ", out_csv)

  # Return results
  return(list(
    match_stats = match_stats,
    time_parse_rate = time_parse_rate,
    concordance_results = concordance_results,
    time_discrepancy = time_discrepancy,
    summary_metrics = summary_tbl
  ))
}

# Run the comparison only if invoked as a script (not sourced as a library)
if (!interactive() && Sys.getenv("TTS_SKIP_MAIN", "0") != "1") {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 1) {
    stop("Usage: Rscript compare_tts.r <config.json>")
  }
  compare_tts(args[1])
}