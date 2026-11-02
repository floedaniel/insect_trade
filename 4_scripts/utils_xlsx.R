# utils_xlsx.R
# write_xlsx_tables(): write one or more data frames to a formatted .xlsx
# (bold frozen header, auto-filter, column widths). Every table in
# 5_outputs/ is also written as .xlsx (project rule).

write_xlsx_tables <- function(sheets, path) {
  stopifnot(is.list(sheets), length(names(sheets)) == length(sheets))
  wb <- openxlsx::createWorkbook()
  head_style <- openxlsx::createStyle(textDecoration = "bold",
                                      fontColour = "#FFFFFF",
                                      fgFill = "#1F4E79", wrapText = TRUE,
                                      valign = "top")
  for (nm in names(sheets)) {
    df <- as.data.frame(sheets[[nm]])
    sheet <- substr(nm, 1, 31)               # Excel sheet-name limit
    openxlsx::addWorksheet(wb, sheet)
    openxlsx::writeData(wb, sheet, df, headerStyle = head_style,
                        withFilter = ncol(df) > 1)
    openxlsx::freezePane(wb, sheet, firstRow = TRUE)
    # width from header and first 200 values, capped
    w <- vapply(seq_along(df), \(i) {
      v <- c(names(df)[i], head(as.character(df[[i]]), 200))
      min(max(nchar(v, keepNA = FALSE), 8) + 2, 60)
    }, numeric(1))
    openxlsx::setColWidths(wb, sheet, cols = seq_along(df), widths = w)
  }
  openxlsx::saveWorkbook(wb, path, overwrite = TRUE)
  invisible(path)
}
